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

# 2. Criar .env.local se nao existir
ENV_FILE="$PROJECT_DIR/.env.local"
if [ ! -f "$ENV_FILE" ]; then
    echo "Criando $ENV_FILE com tokens de teste..."
    cat > "$ENV_FILE" <<'ENVEOF'
# Tokens de teste — usados pelo aiops-agent local
GRAFANA_WEBHOOK_TOKEN=test-grafana-token-local
SLACK_BOT_TOKEN=xoxb-your-test-token-here
SLACK_CHANNEL=#aiops-alerts
ENVEOF
    echo "  Criado: $ENV_FILE"
    echo "  IMPORTANTE: Substitua SLACK_BOT_TOKEN pelo token real do seu workspace de teste"
else
    echo "  .env.local ja existe, mantendo"
fi

# 3. Subir containers
echo ""
echo "Subindo containers..."
cd "$SCRIPT_DIR"
docker compose up -d --build

echo ""
echo "=== Stack pronta! ==="
echo ""
echo "  Grafana:          http://localhost:3000  (admin / aiops123)"
echo "  Prometheus:       http://localhost:9090"
echo "  Metrics Exporter: http://localhost:9101/metrics"
echo "  Slack Mock:       http://localhost:8001  (API: /api/chat.postMessage)"
echo ""
echo "  Dashboard: http://localhost:3000/d/aiops-payment-svc"
echo "  Alerting:  http://localhost:3000/alerting/list"
echo ""
echo "=== Timeline automatica ==="
echo "  0-2 min:  Normal (metricas estaveis)"
echo "  2-2.5min: Degradacao progressiva"
echo "  2.5-5min: Incidente (pool esgotado, erros altos)"
echo "  5+ min:   Recuperacao"
echo ""
echo "=== Controle manual ==="
echo "  curl -X POST http://localhost:9101/trigger-incident  # Forcar incidente"
echo "  curl -X POST http://localhost:9101/reset             # Voltar ao normal"
echo ""
echo "=== Webhook do Grafana ==="
echo "  O Grafana esta configurado para enviar alertas para:"
echo "  POST http://host.docker.internal:8000/webhook"
echo "  Com header: Authorization: Bearer test-grafana-token-local"
echo ""
echo "  Para receber, inicie o aiops-agent webhook server:"
echo "  cd $PROJECT_DIR && python -m server.app"
echo ""
echo "=== Slack Mock ==="
echo "  O Slack Mock simula a API do Slack localmente."
echo "  Configure o SDK para apontar para: http://localhost:8001/api/"
echo "  Mensagens capturadas: GET http://localhost:8001/_mock/messages"
echo "  Limpar mensagens:     DELETE http://localhost:8001/_mock/messages"
echo ""
echo "  Para usar Slack real, siga: infra/SLACK-SETUP.md"
