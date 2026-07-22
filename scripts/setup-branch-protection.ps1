# Configura protecao de branch no GitHub: PR obrigatorio + CI verde.
#
# Uso:
#   .\scripts\setup-branch-protection.ps1 -AllRepos
#   .\scripts\setup-branch-protection.ps1 -Repo paycore
#   .\scripts\setup-branch-protection.ps1 -AllRepos -RequiredReviewCount 0
#
# RequiredReviewCount = 0 (padrao):
#   - PR obrigatorio + CI verde
#   - Voce PODE clicar em Approve no proprio PR (auto-revisao)
# RequiredReviewCount = 1:
#   - GitHub bloqueia auto-aprovacao; o botao Approve nao vale para o autor do PR
#
# Requisitos: gh auth login com permissao admin nos repositorios.

param(
    [string]$Owner = "MariaHilmar",
    [string]$Repo = "",
    [int]$RequiredReviewCount = 0,
    [switch]$AllRepos,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# Checks conhecidos por repo (sobrescreve deteccao automatica)
$KnownChecks = @{
    "paycore"           = @("Lint", "Test")
    "juris-sync"        = @("Lint", "Test", "Integration & Contract Tests (Docker)")
    "juris-sync-web"    = @("quality")
    "maria-portfolio"   = @("build")
    "mgi-kpi-pipeline"  = @("lint", "pytest (3.11)", "pytest (3.12)")
    "sre-agent"         = @("test")
}

function Get-RepoTargets {
    if ($AllRepos) {
        $repos = gh repo list $Owner --limit 200 --json name,defaultBranchRef | ConvertFrom-Json
        return $repos |
            Where-Object { $_.defaultBranchRef.name } |
            ForEach-Object {
                @{
                    Repo   = $_.name
                    Branch = $_.defaultBranchRef.name
                    Checks = if ($KnownChecks.ContainsKey($_.name)) { $KnownChecks[$_.name] } else { @() }
                }
            }
    }

    if ($Repo) {
        $info = gh api "repos/$Owner/$Repo" --jq '{branch:.default_branch}' | ConvertFrom-Json
        return @(@{
                Repo   = $Repo
                Branch = $info.branch
                Checks = if ($KnownChecks.ContainsKey($Repo)) { $KnownChecks[$Repo] } else { @() }
            })
    }

    # portfolio padrao (lista fixa)
    return @(
        @{ Repo = "paycore"; Branch = "main"; Checks = $KnownChecks["paycore"] }
        @{ Repo = "juris-sync"; Branch = "main"; Checks = $KnownChecks["juris-sync"] }
        @{ Repo = "juris-sync-web"; Branch = "feat/dashboard-jurimetria"; Checks = $KnownChecks["juris-sync-web"] }
        @{ Repo = "maria-portfolio"; Branch = "docs/portfolio-hub"; Checks = $KnownChecks["maria-portfolio"] }
        @{ Repo = "mgi-kpi-dashboard"; Branch = "main"; Checks = @() }
        @{ Repo = "mgi-kpi-pipeline"; Branch = "main"; Checks = $KnownChecks["mgi-kpi-pipeline"] }
        @{ Repo = "sre-agent"; Branch = "main"; Checks = $KnownChecks["sre-agent"] }
    )
}

function Get-AutoChecks {
    param([string]$Repository, [string]$Branch)

    try {
        $names = gh api "repos/$Owner/$Repository/commits/$([uri]::EscapeDataString($Branch))/check-runs" `
            --jq '.check_runs[] | select(.name != "SonarCloud Code Analysis") | .name' 2>$null
        if ($names) {
            return @($names -split "`n" | Where-Object { $_ })
        }
    } catch {}
    return @()
}

function Set-BranchProtection {
    param(
        [string]$Repository,
        [string]$Branch,
        [string[]]$Checks,
        [int]$ReviewCount
    )

    if ($Checks.Count -eq 0) {
        $Checks = Get-AutoChecks -Repository $Repository -Branch $Branch
    }

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
        Write-Host "  checks obrigatorios: (nenhum)"
    }
    Write-Host "  aprovacoes necessarias: $ReviewCount"

    if ($DryRun) {
        Write-Host "  [dry-run] PUT $uri" -ForegroundColor Yellow
        return
    }

    $tmp = New-TemporaryFile
    try {
        [System.IO.File]::WriteAllText($tmp.FullName, $json)
        gh api --method PUT $uri --input $tmp.FullName 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "gh api exit $LASTEXITCODE" }
        Write-Host "  protecao aplicada" -ForegroundColor Green
    } catch {
        Write-Host "  ERRO: $_" -ForegroundColor Red
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

$targets = Get-RepoTargets
if (-not $targets) {
    Write-Error "Nenhum repositorio encontrado."
}

Write-Host "GitHub branch protection - $Owner" -ForegroundColor White
Write-Host "Aprovacoes obrigatorias para merge: $RequiredReviewCount" -ForegroundColor White
if ($RequiredReviewCount -eq 0) {
    Write-Host "Modo auto-revisao: voce pode clicar em Approve no proprio PR." -ForegroundColor DarkGray
}

foreach ($item in $targets) {
    Set-BranchProtection -Repository $item.Repo -Branch $item.Branch -Checks @($item.Checks) -ReviewCount $RequiredReviewCount
}

Write-Host "`nConcluido." -ForegroundColor Green
