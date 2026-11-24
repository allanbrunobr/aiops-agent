#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] && source .venv/bin/activate || true
echo "Spec autoritativa: docs/review-fixes-agent-prompt.md (RF1-RF3) + docs/review-qwen-result.md"
echo "Verifique LOCAL M5 DIRETO: .venv/bin/python -m pytest tests -m unit -q ; ruff check . ; mypy domain application core"
echo "NAO use scripts/ci-local.sh unit (venv fresco pesado, m5-guard bloqueia). NAO va pro M1."
echo "Baseline: 1961 unit passed. Smokes V0/V1/MON exit 3. Proxima: RF1."
