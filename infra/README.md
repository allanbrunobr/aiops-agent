# Infra — Ambiente Local de Teste do AIOps Agent

Este diretorio contem toda a infraestrutura necessaria para testar o projeto
AIOps Agent **end-to-end** sem depender de ambientes corporativos. Tudo roda
localmente via Docker + Python.

---

## O que este projeto faz

Simula o ciclo completo de um incidente real:

```
Metrics Exporter  ──scrape 15s──>  Prometheus  ──query──>  Grafana
   (simula                           (armazena              (avalia regras,
   payment-svc)                       metricas)              detecta anomalia)
                                                                  │
                                                        webhook POST (Bearer)
                                                                  │
                                                                  v
                                                          AIOps Agent (:8000)
                                                          FastAPI webhook server
                                                                  │
                                                        normalize → dedup → pipeline
                                                                  │
                                                                  v
                                                           Slack (:cloud: real)
                                                           ou Slack Mock (:8003)
                                                        Block Kit progressivo
```

**Cenario simulado:** O `payment-svc` sofre connection pool exhaustion apos um
deploy ruim (`max_connections` caiu de 200 para 50). O metrics exporter gera as
metricas que reproduzem essa degradacao, o Grafana detecta e dispara webhook, o
AIOps Agent processa e envia notificacao Block Kit no Slack com diagnostico,
evidencias, confianca e proposta de fix.

---

## Pre-requisitos

- **Docker** e **Docker Compose** (v2)
- **Python 3.12+** com venv
- (Opcional) Workspace Slack gratuito para notificacoes reais

---

## Quick Start — 3 passos

### Passo 1: Configurar variaveis de ambiente

Copie o template e edite com seus tokens:

```bash
# Na raiz do projeto (nao dentro de infra/)
cp env.example .env.local
```

Edite `.env.local`:

```env
# Token que o Grafana envia no header Authorization: Bearer <token>
# Deve bater com o configurado em infra/grafana/provisioning/alerting/contact-points.yaml
GRAFANA_WEBHOOK_TOKEN=test-grafana-token-local

# Token do Bot Slack (obtenha seguindo "Configurar Slack" abaixo)
# Se nao tiver, deixe vazio — o sistema funciona sem Slack (fallback local)
SLACK_BOT_TOKEN=xoxb-seu-token-aqui

# Canal do Slack onde as notificacoes serao postadas
SLACK_CHANNEL=#aiops-alerts
```

> **Se voce nao tem um token Slack ainda**, pule para a secao
> [Configurar Slack (workspace gratuito)](#configurar-slack-workspace-gratuito)
> e depois volte aqui.

### Passo 2: Subir a infraestrutura Docker

```bash
docker compose -f infra/docker-compose.yaml up -d --build
```

Verifique se tudo subiu:

| Servico          | URL                              | O que faz                         |
|------------------|----------------------------------|-----------------------------------|
| Prometheus       | http://localhost:9090             | Armazena metricas do exporter     |
| Grafana          | http://localhost:3001             | Dashboard + alertas (admin/aiops123) |
| Metrics Exporter | http://localhost:9101/metrics     | Simula metricas do payment-svc    |
| Slack Mock       | http://localhost:8003/_mock/health| Mock da API Slack (opcional)      |

### Passo 3: Iniciar o webhook server do AIOps Agent

```bash
# Na raiz do projeto
source .venv/bin/activate        # se ainda nao ativou
pip install -r requirements.txt  # se ainda nao instalou

python infra/run_webhook_server.py
```

Saida esperada:
```
============================================================
AIOps Agent — Webhook Server
============================================================
  Listening:    http://0.0.0.0:8000
  Health:       http://localhost:8000/health
  Webhook:      POST http://localhost:8000/webhook
  .env.local:   loaded
  GRAFANA_TOKEN: set
  SLACK_TOKEN:   set
============================================================
```

**Pronto!** Agora e so esperar. O ciclo automatico e:

| Tempo       | Fase        | O que acontece                                      |
|-------------|-------------|-----------------------------------------------------|
| 0 — 2 min   | Normal      | Metricas estaveis, sem alertas                     |
| 2 — 2.5 min | Degradando  | Error rate e latencia sobem gradualmente            |
| 2.5 — 5 min | Incidente   | Pool 100%, error_rate 38%, latencia 950ms           |
| 5+ min      | Recuperacao | Metricas voltam ao normal                           |

Quando o Grafana detectar as anomalias (apos ~2.5min), ele dispara webhook
automaticamente para `http://host.docker.internal:8000/webhook`. Voce vera nos
logs do webhook server:

```
webhook.accepted  alert_severity=critical  alert_title=High Error Rate - payment-svc
```

E no Slack, a notificacao Block Kit aparece no canal `#aiops-alerts`.

---

## Teste manual (sem esperar o ciclo)

### Opcao A — Simular webhook do Grafana

```bash
bash infra/test-webhook.sh
```

Envia um payload identico ao que o Grafana envia. Voce vera `HTTP Status: 202`.

### Opcao B — Forcar incidente no exporter

```bash
# Forcar degradacao imediata (sem esperar 2 min)
curl -X POST http://localhost:9101/trigger-incident

# Voltar ao normal
curl -X POST http://localhost:9101/reset
```

### Opcao C — Teste completo do Slack (Block Kit progressivo)

```bash
python infra/test_slack_notification.py
```

Este script simula o ciclo inteiro que o pipeline faz:
1. `pipeline.started` → posta mensagem inicial no Slack
2. `node.completed` x11 → atualiza a mesma mensagem com checklist progressivo
3. `pipeline.completed` → atualiza com diagnostico completo (root cause, evidencias, fix)

Saida esperada:
```
[1/4] Enviando pipeline.started para #aiops-alerts...
slack.message_posted  channel_id=C0AL9T4S8QJ  ts=1773347770.080689
[2/4] Node 1/11: telemetry_sanitizer (45ms)
[2/4] Node 2/11: log_parser (320ms)
...
[2/4] Node 11/11: postmortem_generator (380ms)
[3/4] Enviando pipeline.completed com diagnostico...
slack.final_message  correlation_id=inc-20260312-demo0001
[4/4] Notificacao completa enviada!
```

---

## Configurar Slack (workspace gratuito)

Para receber notificacoes reais no Slack (e nao apenas no mock local):

### 1. Criar workspace

1. Acesse https://slack.com/get-started#/createnew
2. Use qualquer email (e gratuito)
3. Nome sugerido: `AIOps Lab`
4. Crie o canal `#aiops-alerts`

### 2. Criar Slack App (Bot)

1. Acesse https://api.slack.com/apps
2. **Create New App** → **From scratch**
3. Nome: `AIOps Agent Bot` | Workspace: o que voce criou
4. No menu lateral, va em **OAuth & Permissions**
5. Em **Bot Token Scopes**, adicione:
   - `chat:write` — postar mensagens
   - `chat:write.public` — postar em canais publicos sem ser membro
6. Clique **Install to Workspace** (topo da pagina) e autorize
7. Copie o **Bot User OAuth Token** (comeca com `xoxb-`)

### 3. Configurar no projeto

Edite `.env.local` na raiz:

```env
SLACK_BOT_TOKEN=xoxb-seu-token-real-aqui
SLACK_CHANNEL=#aiops-alerts
```

### 4. Validar

```bash
# Teste rapido — deve retornar {"ok": true, ...}
curl -s -X POST https://slack.com/api/auth.test \
  -H "Authorization: Bearer xoxb-seu-token-real-aqui" | python -m json.tool
```

> **Nota tecnica:** O `chat.postMessage` aceita nome do canal (`#aiops-alerts`),
> mas o `chat.update` exige o **channel ID** (ex: `C0AL9T4S8QJ`). O
> `SlackNotifier` resolve isso automaticamente — captura o ID da resposta do
> primeiro `postMessage` e usa nos `update` subsequentes.

---

## Alternativa: usar Slack Mock (sem workspace real)

Se voce nao quiser criar um workspace Slack, o stack Docker ja inclui um
servidor mock que simula a API do Slack:

```bash
# O mock ja esta rodando em http://localhost:8003
# Configure o SDK para apontar para ele:
export SLACK_API_BASE_URL=http://localhost:8003/api/
export SLACK_BOT_TOKEN=xoxb-mock-token
```

Ver mensagens capturadas:
```bash
curl http://localhost:8003/_mock/messages | python -m json.tool
```

Limpar mensagens:
```bash
curl -X DELETE http://localhost:8003/_mock/messages
```

---

## Estrutura dos arquivos

```
infra/
├── README.md                         ← voce esta aqui
├── docker-compose.yaml               ← orquestra os 4 containers
├── setup.sh                          ← script automatizado (alternativa ao quick start manual)
│
├── prometheus/
│   └── prometheus.yaml               ← config: scrape metrics-exporter a cada 15s
│
├── grafana/provisioning/
│   ├── datasources/
│   │   └── prometheus.yaml           ← registra Prometheus como datasource (uid: prometheus)
│   ├── alerting/
│   │   ├── alert-rules.yaml          ← 3 regras: error rate, pool exhaustion, latencia p95
│   │   ├── contact-points.yaml       ← webhook → http://host.docker.internal:8000/webhook
│   │   └── notification-policies.yaml← roteia todos os alertas para o webhook
│   └── dashboards/
│       ├── dashboard-provider.yaml   ← auto-provisiona dashboards do diretorio json/
│       └── json/payment-svc.json     ← dashboard com 5 paineis do payment-svc
│
├── metrics-exporter/
│   ├── Dockerfile                    ← python:3.12-slim
│   └── exporter.py                   ← servidor HTTP :9101, simula ciclo de incidente
│
├── slack-mock/
│   ├── Dockerfile                    ← python:3.12-slim + fastapi + uvicorn
│   └── slack_mock_server.py          ← mock: chat.postMessage, chat.update, auth.test
│
├── run_webhook_server.py             ← boot do FastAPI app com webhook router + deps
├── test-webhook.sh                   ← simula POST do Grafana (curl)
├── test_slack_notification.py        ← teste completo do ciclo Slack Block Kit
└── SLACK-SETUP.md                    ← guia resumido do setup Slack
```

---

## Como funciona por dentro

### Metrics Exporter (`metrics-exporter/exporter.py`)

Servidor HTTP puro (sem frameworks) que expoe metricas no formato Prometheus em
`GET /metrics`. Simula 4 fases automaticas de um incidente no `payment-svc`:

| Metrica                              | Normal    | Incidente  |
|--------------------------------------|-----------|------------|
| `payment_svc_error_rate_percent`     | ~1%       | ~38%       |
| `payment_svc_connection_pool_active` | ~60/200   | 200/200    |
| `payment_svc_latency_ms` (p95)       | ~45ms     | ~950ms     |
| `payment_svc_requests_total`         | ~150 req/s| ~150 req/s |

Controle manual:
- `POST /trigger-incident` — forca degradacao imediata
- `POST /reset` — volta ao estado normal

### Prometheus (`prometheus/prometheus.yaml`)

Scrape simples: coleta metricas do exporter a cada 15s com label `service=payment-svc`.

### Grafana (provisioning completo)

Tudo e provisionado automaticamente no startup (sem configuracao manual):

- **Datasource:** Prometheus com `uid: prometheus` (critico — as alert rules referenciam esse UID)
- **3 Alert Rules:**
  - High Error Rate > 10% por 30s → severity: critical
  - Connection Pool > 80% por 15s → severity: critical
  - Latency P95 > 500ms por 30s → severity: warning
- **Contact Point:** Webhook POST para `http://host.docker.internal:8000/webhook` com `Authorization: Bearer test-grafana-token-local`
- **Notification Policy:** Roteia todos os alertas para o contact point webhook
- **Dashboard:** 5 paineis (Error Rate, Connection Pool, Latency, CPU/Memory, Incident Phase)

### Webhook Server (`run_webhook_server.py`)

FastAPI app que monta o pipeline completo:

1. Recebe POST em `/webhook`
2. Identifica a fonte (Grafana detectado pelo campo `alerts` no payload)
3. Valida autenticacao (Bearer token vs `GRAFANA_WEBHOOK_TOKEN`)
4. Normaliza payload (Grafana → `NormalizedAlert`)
5. Deduplicacao por fingerprint (janela de 5min)
6. Submete ao pipeline runner (max 3 concorrentes, fila de 10)

### SlackNotifier (`server/notifications/slack.py`)

Notificacoes Block Kit progressivas:

1. `pipeline.started` → `chat.postMessage` com titulo, severidade, servico
2. `node.completed` x11 → `chat.update` com checklist progressivo (checkmarks verdes)
3. `pipeline.completed` → `chat.update` final com:
   - Root cause
   - Evidencias
   - Confianca (com emoji de cor)
   - Risk level e blast radius
   - Proposed fix (em bloco de codigo)

---

## Troubleshooting

### "Grafana alerts not firing"

1. Verifique se o Prometheus esta coletando: http://localhost:9090/targets
   (deve mostrar `metrics-exporter:9101` como UP)
2. Verifique as regras: http://localhost:3001/alerting/list (admin/aiops123)
3. Force incidente: `curl -X POST http://localhost:9101/trigger-incident`
4. Logs do Grafana: `docker logs aiops-grafana 2>&1 | grep -i alert`

### "webhook.rejected — unknown_source"

O webhook server nao conseguiu identificar a fonte. O Grafana e detectado
automaticamente pelo campo `alerts` (array) no payload. Se estiver testando
manualmente, inclua o header `Alert-Source: grafana`.

### "webhook.rejected — auth_failed"

O token no header `Authorization: Bearer <token>` nao bate com o
`GRAFANA_WEBHOOK_TOKEN` no `.env.local`. Ambos devem ser iguais (default:
`test-grafana-token-local`).

### "chat.update channel_not_found" no Slack

Isso acontecia porque `chat.update` exige **channel ID** (ex: `C0AL9T4S8QJ`),
nao o nome do canal. Ja foi corrigido — o `SlackNotifier` agora captura o
channel ID da resposta do `chat.postMessage` e reutiliza nos updates.

### Portas em conflito

Se as portas 9090, 3001, 9101 ou 8003 estiverem ocupadas:

```bash
# Descobrir o que esta usando a porta
lsof -i :3001

# Alterar no docker-compose.yaml (formato host:container)
# Ex: mudar Grafana de 3001:3000 para 3002:3000
```

### host.docker.internal nao resolve (Linux)

O `docker-compose.yaml` ja inclui `extra_hosts: host.docker.internal:host-gateway`
para Linux. Se ainda nao funcionar:

```bash
# Testar conectividade do container Grafana para o host
docker exec aiops-grafana wget -qO- http://host.docker.internal:8000/health
```

---

## Teardown

```bash
# Parar e remover containers + volumes
docker compose -f infra/docker-compose.yaml down -v

# Parar o webhook server
# Ctrl+C no terminal onde esta rodando
```

---

## Relacao com o projeto principal

Esta infraestrutura **nao e o AIOps Agent em si** — e um ambiente de teste que
simula o mundo externo (Grafana, Prometheus, metricas) para validar que o
pipeline do agent funciona end-to-end.

O projeto principal (`main.py`) roda o pipeline completo com LLM:

```bash
# Pipeline completo com Claude (requer ANTHROPIC_API_KEY no .env)
python main.py

# Modo heuristico (sem LLM, sem custos de API)
python main.py --no-llm

# Modo sem RAG (startup mais rapido)
python main.py --no-rag
```

A diferenca:

| Componente           | `main.py` (projeto principal)        | `infra/` (teste local)              |
|----------------------|--------------------------------------|--------------------------------------|
| Dados de entrada     | Simulados em `data/sample_incident.py`| Gerados pelo metrics exporter Docker |
| Fonte de alertas     | Interno (detector heuristico)        | Grafana via webhook HTTP             |
| Pipeline             | Completo (LLM + RAG + safety)        | Webhook → normalizer → dedup         |
| Notificacao          | Output em `rich` (terminal)          | Slack Block Kit (real ou mock)       |
| Requer LLM           | Sim (ou `--no-llm`)                  | Nao                                  |
