#!/bin/bash
# Simula um webhook do Grafana Alerting para o aiops-agent local
# Usa o mesmo formato que o Grafana Unified Alerting envia

WEBHOOK_URL="${1:-http://localhost:8000/webhook}"
TOKEN="test-grafana-token-local"

echo "Enviando alerta Grafana simulado para $WEBHOOK_URL ..."

curl -s -w "\nHTTP Status: %{http_code}\n" \
  -X POST "$WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Alert-Source: grafana" \
  -d '{
    "status": "firing",
    "alerts": [
      {
        "status": "firing",
        "labels": {
          "alertname": "High Error Rate - payment-svc",
          "severity": "critical",
          "service": "payment-svc",
          "component": "connection-pool",
          "environment": "production"
        },
        "annotations": {
          "summary": "Error rate at 38.2% (threshold: 10%)",
          "description": "payment-svc error rate exceeded 10% for 30s. Current: 38.2%. Possible connection pool exhaustion."
        },
        "startsAt": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'",
        "endsAt": "0001-01-01T00:00:00Z",
        "fingerprint": "abc123def456",
        "generatorURL": "http://localhost:3000/alerting/grafana/payment-svc-high-error-rate/view"
      }
    ],
    "commonLabels": {
      "alertname": "High Error Rate - payment-svc",
      "severity": "critical",
      "service": "payment-svc"
    },
    "externalURL": "http://localhost:3000",
    "version": "1",
    "groupKey": "{}:{alertname=\"High Error Rate - payment-svc\"}"
  }'

echo ""
echo "Pronto! Verifique os logs do aiops-agent."
