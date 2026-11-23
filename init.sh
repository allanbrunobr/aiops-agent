#!/usr/bin/env bash
# Fast session startup for long-run workers.
# BEFORE implementing: read docs/vigia-barato-agent-prompt.md + ROBUSTNESS_PLAN.md (Eixo D).
set -euo pipefail
cd "$(dirname "$0")"
if [ -d .venv ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
echo "[init] python: $(python --version 2>&1)"
echo "[init] spec autoritativa: docs/vigia-barato-agent-prompt.md + ROBUSTNESS_PLAN.md (Eixo D :309-363)"
echo "[init] baseline verify: .venv/bin/python -m pytest tests -m unit -q"
echo "[init] ok"