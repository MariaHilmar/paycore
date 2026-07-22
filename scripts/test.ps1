# PayCore - roda lint e testes localmente (Windows PowerShell)
#
# Uso:
#   .\scripts\test.ps1              # lint + testes com cobertura
#   .\scripts\test.ps1 -Quick       # so testes, sem cobertura
#   .\scripts\test.ps1 -LintOnly    # so ruff + black
#   .\scripts\test.ps1 -SkipLint    # so pytest
#
# Requisitos:
#   - .venv com pip install -e ".[dev]"
#   - PostgreSQL em localhost:5432 com banco paycore_test
#     (Docker: docker compose up db -d e criar paycore_test, ou Postgres local)

param(
    [switch]$Quick,
    [switch]$LintOnly,
    [switch]$SkipLint
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $Python = $VenvPython
    Write-Host "Python: $Python (venv)" -ForegroundColor DarkGray
} else {
    $Python = "python"
    Write-Host "Aviso: .venv nao encontrado, usando python do PATH" -ForegroundColor Yellow
}

if (-not $env:TEST_DATABASE_URL) {
    $env:TEST_DATABASE_URL = "postgresql+psycopg://paycore:paycore@localhost:5432/paycore_test"
}
Write-Host "TEST_DATABASE_URL: $env:TEST_DATABASE_URL" -ForegroundColor DarkGray

function Test-PostgresConnection {
    & $Python -c @"
import sys
try:
    import psycopg
    psycopg.connect('$($env:TEST_DATABASE_URL.Replace('+psycopg', ''))').close()
except Exception as exc:
    print(f'ERRO: nao conectou ao Postgres: {exc}', file=sys.stderr)
    print('', file=sys.stderr)
    print('Opcoes:', file=sys.stderr)
    print('  1. Postgres local: crie o banco paycore_test (usuario paycore/paycore)', file=sys.stderr)
    print('  2. Docker: abra Docker Desktop e rode: docker compose up db -d', file=sys.stderr)
    sys.exit(1)
"@
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    Write-Host "Postgres: OK" -ForegroundColor Green
}

if (-not $LintOnly) {
    Test-PostgresConnection
}

if (-not $SkipLint) {
    Write-Host "`n--- Ruff ---" -ForegroundColor Cyan
    & $Python -m ruff check app/ tests/
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    Write-Host "`n--- Black ---" -ForegroundColor Cyan
    & $Python -m black --check app/ tests/
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($LintOnly) {
    Write-Host "`nLint OK" -ForegroundColor Green
    exit 0
}

Write-Host "`n--- Pytest ---" -ForegroundColor Cyan
if ($Quick) {
    & $Python -m pytest tests/ -q
} else {
    & $Python -m pytest tests/ -v --cov=app --cov-report=term-missing
}
exit $LASTEXITCODE
