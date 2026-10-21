#!/usr/bin/env bash
# scripts/ci-local.sh — replica os jobs do GitHub Actions localmente.
#
# Uso:
#   scripts/ci-local.sh                 # roda os 3 jobs (unit, integration, rust)
#   scripts/ci-local.sh unit            # só unit-tests
#   scripts/ci-local.sh integration     # só integration & snapshot
#   scripts/ci-local.sh rust            # só rust-build & PyO3
#
# Cria venvs descartáveis em /tmp/aiops-ci-venv-<job> para mimicar o ambiente
# limpo do runner do GitHub Actions. O .venv local do projeto não é tocado.
#
# Override do interpretador Python:
#   PYTHON=python3.12 scripts/ci-local.sh unit

set -euo pipefail

JOB="${1:-all}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python3.12}"
VENV_BASE="/tmp/aiops-ci-venv"

# Deps do job unit-tests — devem ficar idênticas a .github/workflows/ci.yml
UNIT_DEPS=(
  "pytest>=9.0.0"
  "pytest-mock>=3.14.0"
  "pytest-asyncio>=0.24.0"
  "anyio[trio]>=4.0.0"
  "pydantic>=2.0.0"
  "pydantic-settings[yaml]>=2.0.0"
  "PyYAML>=6.0.0"
  "structlog>=24.1.0"
  "fastapi>=0.115.0"
  "httpx>=0.27.0"
  "PyJWT>=2.8.0"
  "cryptography>=42.0"
  "python-dotenv>=1.0.0"
  "rich>=13.0.0"
  "numpy<2.0.0"
  "drain3>=0.9.11"
  "anthropic>=0.40.0"
  "databricks-sdk>=0.38.0"
  "requests>=2.28.0"
  "slack-sdk>=3.27.0"
  "aiohttp>=3.9.0"
  "mcp>=1.0.0,<2.0.0"
)

require() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "ERRO: '$1' não encontrado no PATH." >&2
    return 1
  }
}

mkvenv() {
  local venv="$1"
  rm -rf "$venv"
  "$PYTHON" -m venv "$venv"
  # shellcheck disable=SC1091
  source "$venv/bin/activate"
  pip install --quiet --upgrade pip
}

run_unit() {
  echo ">>> Job: unit-tests"
  require "$PYTHON"
  mkvenv "$VENV_BASE-unit"
  pip install --quiet "${UNIT_DEPS[@]}"
  cd "$ROOT"
  pytest tests/ -m unit --ignore=tests/rust -v
  deactivate
}

run_integration() {
  echo ">>> Job: integration-tests"
  require "$PYTHON"
  mkvenv "$VENV_BASE-integration"
  pip install --quiet -r "$ROOT/requirements.txt"
  cd "$ROOT"
  pytest tests/ -m "integration or snapshot" -v
  deactivate
}

run_rust() {
  echo ">>> Job: rust-build"
  require "$PYTHON"
  require cargo
  mkvenv "$VENV_BASE-rust"
  pip install --quiet -r "$ROOT/requirements.txt" "maturin>=1.4.0"
  cd "$ROOT"
  cargo test --manifest-path rust/Cargo.toml
  maturin build --manifest-path rust/Cargo.toml --release
  pip install --quiet --force-reinstall rust/target/wheels/*.whl
  python -c "import _aiops_rust; print('rust ext OK:', dir(_aiops_rust))"
  python -m pytest tests/ -v
  deactivate
}

case "$JOB" in
  unit)        run_unit ;;
  integration) run_integration ;;
  rust)        run_rust ;;
  all)         run_unit; run_integration; run_rust ;;
  -h|--help)
    sed -n '2,16p' "$0"
    exit 0
    ;;
  *)
    echo "Uso: $0 [unit|integration|rust|all]" >&2
    exit 2
    ;;
esac

echo
echo "OK Job '${JOB}' concluído."
