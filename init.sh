#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
[ -d .venv ] && source .venv/bin/activate
echo "Spec: docs/tech-debt-agent-prompt.md (TD1 stub Rust, TD2 seeds 50, TD3 docs circularidade)"
echo "Verificação LOCAL M5: .venv/bin/python -m pytest tests -m unit -q  (NÃO ci-local.sh unit, NÃO M1)"
echo "pi-lens: node ~/.pi/agent/npm/node_modules/pi-lens/dist/mcp/worker.js --file=<abs> --cwd=<abs>"
