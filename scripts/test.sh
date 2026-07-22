#!/usr/bin/env bash
# PayCore - roda lint e testes localmente (Linux/macOS/Git Bash)
#
# Uso:
#   ./scripts/test.sh              # lint + testes com cobertura
#   ./scripts/test.sh --quick      # so testes
#   ./scripts/test.sh --lint-only  # so ruff + black
#   ./scripts/test.sh --skip-lint  # so pytest

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
  echo "Python: $PYTHON (venv)"
else
  PYTHON="python3"
  echo "Aviso: .venv nao encontrado, usando python3 do PATH"
fi

export TEST_DATABASE_URL="${TEST_DATABASE_URL:-postgresql+psycopg://paycore:paycore@localhost:5432/paycore_test}"
echo "TEST_DATABASE_URL: $TEST_DATABASE_URL"

QUICK=0
LINT_ONLY=0
SKIP_LINT=0
for arg in "$@"; do
  case "$arg" in
    --quick) QUICK=1 ;;
    --lint-only) LINT_ONLY=1 ;;
    --skip-lint) SKIP_LINT=1 ;;
  esac
done

if [[ "$LINT_ONLY" -eq 0 ]]; then
  "$PYTHON" -c "
import sys
try:
    import psycopg
    psycopg.connect('${TEST_DATABASE_URL/+psycopg/}').close()
except Exception as exc:
    print(f'ERRO: nao conectou ao Postgres: {exc}', file=sys.stderr)
    sys.exit(1)
"
  echo "Postgres: OK"
fi

if [[ "$SKIP_LINT" -eq 0 ]]; then
  echo ""
  echo "--- Ruff ---"
  "$PYTHON" -m ruff check app/ tests/
  echo ""
  echo "--- Black ---"
  "$PYTHON" -m black --check app/ tests/
fi

if [[ "$LINT_ONLY" -eq 1 ]]; then
  echo ""
  echo "Lint OK"
  exit 0
fi

echo ""
echo "--- Pytest ---"
if [[ "$QUICK" -eq 1 ]]; then
  "$PYTHON" -m pytest tests/ -q
else
  "$PYTHON" -m pytest tests/ -v --cov=app --cov-report=term-missing
fi
