#!/bin/bash
# AIOps Agent — Setup of the local test infrastructure
# Starts Grafana + Prometheus + Metrics Exporter + Slack Mock
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== AIOps Agent — Local Infrastructure ==="
echo ""

# 1. Check Docker
if ! command -v docker &>/dev/null; then
    echo "ERROR: Docker not found. Install it from https://docker.com"
    exit 1
fi

if ! docker compose version &>/dev/null; then
    echo "ERROR: Docker Compose v2 not found."
    exit 1
fi

# 2. Create .env.local if it does not exist
ENV_FILE="$PROJECT_DIR/.env.local"
if [ ! -f "$ENV_FILE" ]; then
    echo "Creating $ENV_FILE from the template..."
    cp "$PROJECT_DIR/.env.local.example" "$ENV_FILE" 2>/dev/null || cat > "$ENV_FILE" <<'ENVEOF'
# AIOps Agent — Environment variables for local testing
# Documentation: infra/README.md

# Webhook authentication token (must match Grafana contact-points.yaml)
GRAFANA_WEBHOOK_TOKEN=test-grafana-token-local

# Slack Bot Token (get it at https://api.slack.com/apps → OAuth & Permissions)
# Leave empty to use the local fallback (no Slack)
SLACK_BOT_TOKEN=

# Slack channel for notifications
SLACK_CHANNEL=#aiops-alerts
ENVEOF
    echo "  Created: $ENV_FILE"
    echo ""
    echo "  >>> IMPORTANT: Edit $ENV_FILE and fill in SLACK_BOT_TOKEN"
    echo "  >>> Follow the instructions in infra/README.md (section 'Configure Slack')"
    echo ""
else
    echo "  .env.local already exists, keeping it"
fi

# 3. Install Python dependencies if needed
if [ -d "$PROJECT_DIR/.venv" ]; then
    echo "Checking Python dependencies..."
    "$PROJECT_DIR/.venv/bin/pip" install -q structlog slack_sdk fastapi uvicorn pyyaml pydantic-settings aiohttp 2>/dev/null || true
fi

# 4. Start containers
echo ""
echo "Starting Docker containers..."
cd "$SCRIPT_DIR"
docker compose up -d --build

echo ""
echo "=== Stack ready! ==="
echo ""
echo "  Grafana:          http://localhost:3001  (admin / aiops123)"
echo "  Prometheus:       http://localhost:9090"
echo "  Metrics Exporter: http://localhost:9101/metrics"
echo "  Slack Mock:       http://localhost:47892 (API: /api/chat.postMessage)"
echo ""
echo "  Dashboard: http://localhost:3001/d/aiops-payment-svc"
echo "  Alerting:  http://localhost:3001/alerting/list"
echo ""
echo "=== Next step ==="
echo ""
echo "  Start the webhook server (in another terminal):"
echo ""
echo "    cd $PROJECT_DIR"
echo "    source .venv/bin/activate"
echo "    python infra/run_webhook_server.py"
echo ""
echo "=== Automatic incident timeline ==="
echo ""
echo "  0 — 2 min:   Normal (stable metrics, no alerts)"
echo "  2 — 2.5 min: Progressive degradation"
echo "  2.5 — 5 min: Incident (pool exhausted, high errors, alerts fire)"
echo "  5+ min:      Recovery"
echo ""
echo "=== Manual control ==="
echo ""
echo "  curl -X POST http://localhost:9101/trigger-incident  # Force incident"
echo "  curl -X POST http://localhost:9101/reset             # Return to normal"
echo "  bash infra/test-webhook.sh                           # Simulate webhook"
echo "  python infra/test_slack_notification.py              # Full Slack test"
echo ""
