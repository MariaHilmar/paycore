# Configura protecao de branch no GitHub: PR obrigatorio + CI verde + 1 aprovacao.
#
# Uso:
#   .\scripts\setup-branch-protection.ps1              # todos os repos do portfolio
#   .\scripts\setup-branch-protection.ps1 -Repo paycore
#
# Requisitos: gh auth login com permissao admin nos repositorios.
# Nota: com 1 aprovacao obrigatoria, o autor do PR nao pode aprovar o proprio PR.
#       Use Bugbot/Cursor, um colaborador ou ajuste RequiredReviewCount para 0.

param(
    [string]$Owner = "MariaHilmar",
    [string]$Repo = "",
    [int]$RequiredReviewCount = 1,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# Repo -> branch padrao -> jobs do GitHub Actions que devem passar antes do merge
$Portfolio = @(
    @{
        Repo    = "paycore"
        Branch  = "main"
        Checks  = @("Lint", "Test")
    },
    @{
        Repo    = "juris-sync"
        Branch  = "main"
        Checks  = @("Lint", "Test", "Integration & Contract Tests (Docker)")
    },
    @{
        Repo    = "juris-sync-web"
        Branch  = "feat/dashboard-jurimetria"
        Checks  = @("quality")
    },
    @{
        Repo    = "maria-portfolio"
        Branch  = "docs/portfolio-hub"
        Checks  = @("build")
    },
    @{
        Repo    = "mgi-kpi-dashboard"
        Branch  = "main"
        Checks  = @()
    },
    @{
        Repo    = "mgi-kpi-pipeline"
        Branch  = "main"
        Checks  = @()
    },
    @{
        Repo    = "sre-agent"
        Branch  = "main"
        Checks  = @("test")
    }
)

$targets = if ($Repo) {
    $Portfolio | Where-Object { $_.Repo -eq $Repo }
} else {
    $Portfolio
}

if (-not $targets) {
    Write-Error "Repo '$Repo' nao esta na lista do portfolio."
}

function Set-BranchProtection {
    param(
        [string]$Repository,
        [string]$Branch,
        [string[]]$Checks,
        [int]$ReviewCount
    )

    $checksPayload = @(
        foreach ($name in $Checks) {
            @{ context = $name }
        }
    )

    $body = @{
        required_status_checks = @{
            strict = $true
            checks = $checksPayload
        }
        enforce_admins = $false
        required_pull_request_reviews = @{
            dismiss_stale_reviews = $true
            require_code_owner_reviews = $false
            required_approving_review_count = $ReviewCount
        }
        restrictions = $null
        required_linear_history = $false
        allow_force_pushes = $false
        allow_deletions = $false
        block_creations = $false
        required_conversation_resolution = $true
    }

    $json = $body | ConvertTo-Json -Depth 6 -Compress
    $uri = "repos/$Owner/$Repository/branches/$([uri]::EscapeDataString($Branch))/protection"

    Write-Host "`n[$Repository] branch '$Branch'" -ForegroundColor Cyan
    if ($Checks.Count -gt 0) {
        Write-Host "  checks obrigatorios: $($Checks -join ', ')"
    } else {
        Write-Host "  checks obrigatorios: (nenhum - so PR + revisao)"
    }
    Write-Host "  aprovacoes necessarias: $ReviewCount"

    if ($DryRun) {
        Write-Host "  [dry-run] PUT $uri" -ForegroundColor Yellow
        Write-Host "  $json" -ForegroundColor DarkGray
        return
    }

    $tmp = New-TemporaryFile
    try {
        [System.IO.File]::WriteAllText($tmp.FullName, $json)
        gh api --method PUT $uri --input $tmp.FullName | Out-Null
        Write-Host "  protecao aplicada" -ForegroundColor Green
    } catch {
        Write-Host "  ERRO: $_" -ForegroundColor Red
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "GitHub branch protection - $Owner" -ForegroundColor White
Write-Host "Revisao obrigatoria: $RequiredReviewCount aprovacao(oes)" -ForegroundColor White

foreach ($item in $targets) {
    Set-BranchProtection -Repository $item.Repo -Branch $item.Branch -Checks $item.Checks -ReviewCount $RequiredReviewCount
}

Write-Host "`nConcluido." -ForegroundColor Green
Write-Host "Fluxo: branch de feature -> PR -> CI verde -> revisao -> merge" -ForegroundColor DarkGray
