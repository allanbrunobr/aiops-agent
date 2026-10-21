#!/bin/bash
# AIOps Agent — Setup da infraestrutura local de teste
# Sobe Grafana + Prometheus + Metrics Exporter + Slack Mock
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== AIOps Agent — Infraestrutura Local ==="
echo ""

# 1. Verificar Docker
if ! command -v docker &>/dev/null; then
    echo "ERRO: Docker nao encontrado. Instale em https://docker.com"
    exit 1
fi

if ! docker compose version &>/dev/null; then
    echo "ERRO: Docker Compose v2 nao encontrado."
    exit 1
fi

# 2. Criar .env.local se nao existir
ENV_FILE="$PROJECT_DIR/.env.local"
if [ ! -f "$ENV_FILE" ]; then
    echo "Criando $ENV_FILE a partir do template..."
    cp "$PROJECT_DIR/.env.local.example" "$ENV_FILE" 2>/dev/null || cat > "$ENV_FILE" <<'ENVEOF'
# AIOps Agent — Variaveis de ambiente para teste local
# Documentacao: infra/README.md

# Token de autenticacao do webhook (deve bater com contact-points.yaml do Grafana)
GRAFANA_WEBHOOK_TOKEN=test-grafana-token-local

# Slack Bot Token (obtenha em https://api.slack.com/apps → OAuth & Permissions)
# Deixe vazio para usar fallback local (sem Slack)
SLACK_BOT_TOKEN=

# Canal do Slack para notificacoes
SLACK_CHANNEL=#aiops-alerts
ENVEOF
    echo "  Criado: $ENV_FILE"
    echo ""
    echo "  >>> IMPORTANTE: Edite $ENV_FILE e preencha o SLACK_BOT_TOKEN"
    echo "  >>> Siga as instrucoes em infra/README.md (secao 'Configurar Slack')"
    echo ""
else
    echo "  .env.local ja existe, mantendo"
fi

# 3. Instalar dependencias Python se necessario
if [ -d "$PROJECT_DIR/.venv" ]; then
    echo "Verificando dependencias Python..."
    "$PROJECT_DIR/.venv/bin/pip" install -q structlog slack_sdk fastapi uvicorn pyyaml pydantic-settings aiohttp 2>/dev/null || true
fi

# 4. Subir containers
echo ""
echo "Subindo containers Docker..."
cd "$SCRIPT_DIR"
docker compose up -d --build

echo ""
echo "=== Stack pronta! ==="
echo ""
echo "  Grafana:          http://localhost:3001  (admin / aiops123)"
echo "  Prometheus:       http://localhost:9090"
echo "  Metrics Exporter: http://localhost:9101/metrics"
echo "  Slack Mock:       http://localhost:47892 (API: /api/chat.postMessage)"
echo ""
echo "  Dashboard: http://localhost:3001/d/aiops-payment-svc"
echo "  Alerting:  http://localhost:3001/alerting/list"
echo ""
echo "=== Proximo passo ==="
echo ""
echo "  Inicie o webhook server (em outro terminal):"
echo ""
echo "    cd $PROJECT_DIR"
echo "    source .venv/bin/activate"
echo "    python infra/run_webhook_server.py"
echo ""
echo "=== Timeline automatica do incidente ==="
echo ""
echo "  0 — 2 min:   Normal (metricas estaveis, sem alertas)"
echo "  2 — 2.5 min: Degradacao progressiva"
echo "  2.5 — 5 min: Incidente (pool esgotado, erros altos, alertas disparam)"
echo "  5+ min:      Recuperacao"
echo ""
echo "=== Controle manual ==="
echo ""
echo "  curl -X POST http://localhost:9101/trigger-incident  # Forcar incidente"
echo "  curl -X POST http://localhost:9101/reset             # Voltar ao normal"
echo "  bash infra/test-webhook.sh                           # Simular webhook"
echo "  python infra/test_slack_notification.py              # Testar Slack completo"
echo ""
