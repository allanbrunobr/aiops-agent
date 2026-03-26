#!/usr/bin/env bash
# Compila a extensão Rust e instala no .venv correto.
# Necessário porque CONDA_PREFIX no ambiente faz maturin instalar no anaconda base.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO/.venv"
PYTHON="$VENV/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "Erro: $PYTHON não encontrado. Crie o venv primeiro." >&2
  exit 1
fi

RELEASE="${1:-}"

ARGS=("--manifest-path" "$REPO/rust/Cargo.toml")
[[ "$RELEASE" == "--release" ]] && ARGS+=("--release")

echo "Compilando _aiops_rust${RELEASE:+ (release)} → $VENV"
env -u CONDA_PREFIX -u CONDA_DEFAULT_ENV \
    VIRTUAL_ENV="$VENV" \
    "$PYTHON" -m maturin develop "${ARGS[@]}"
echo "Pronto."
