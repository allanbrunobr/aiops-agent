#!/usr/bin/env bash
# Inicialização da sessão long-run — Robustez Eixos A/B/C.
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] && source .venv/bin/activate || true
echo "=== SPEC AUTORITATIVA ==="
echo "1. docs/robustness-abc-agent-prompt.md (regras + mapa tarefa→seção)"
echo "2. ROBUSTNESS_PLAN.md (spec técnica file:line por tarefa; §0/§1 contexto)"
echo "3. claude-progress.txt (estado + regra de verificação)"
echo "=== VERIFICAÇÃO (AQUI no M5, direto .venv — NÃO ci-local.sh unit, NÃO M1) ==="
echo "unit:        .venv/bin/python -m pytest tests -m unit -q"
echo "integration: .venv/bin/python -m pytest tests -m integration -q"
echo "real-rag:    .venv/bin/python -m pytest tests -m real_rag -v"
echo "rust:        cargo test --manifest-path rust/Cargo.toml + clippy -D warnings"
echo "lint/tipos:  .venv/bin/ruff check . + .venv/bin/mypy domain application core"
echo "smoke:       .venv/bin/python main.py --no-llm --no-rag --output-dir /tmp/smoke-out"
