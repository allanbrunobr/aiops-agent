#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] && source .venv/bin/activate || true
echo "Spec autoritativa: docs/continuous-monitor-agent-prompt.md (OP1-OP4)"
echo "Verifique LOCAL M5: scripts/ci-local.sh unit ; ruff check . ; mypy domain application core"
echo "Baseline: ~1909+ unit passed. Smokes V0/V1 exit 3. Próxima: OP1."
