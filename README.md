# AIOps Agent

Plataforma de resposta autonoma a incidentes usando LLM + RAG + maquina de estados multi-agente.

Processa dados de observabilidade (logs, metricas, traces) atraves de um pipeline completo:
sanitizacao → pre-processamento → compressao de contexto → busca semantica FAISS →
diagnostico ReAct multi-agente → proposta de mitigacao → verificacao de seguranca →
geracao de postmortem.

Baseado em 7 papers academicos (2024-2025): STRATUS, OpsAgent, RCACopilot, Acon², AOI, TAMO e "When AIOps Become AI Oops".

---

## Como funciona

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          AIOps Agent Pipeline                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Alerta (Grafana/PagerDuty/Databricks)                                 │
│    │                                                                    │
│    ▼                                                                    │
│  Webhook Server (:8000)                                                │
│    │  identify_source → validate_auth → normalize → dedup              │
│    ▼                                                                    │
│  TelemetrySanitizer ── bloqueia payloads adversariais (reward-hacking) │
│    ▼                                                                    │
│  Aggregator ── Drain3 (logs) + 3-sigma (metricas) + p95 (traces)      │
│    ▼                                                                    │
│  ContextCompressor ── compressao por prioridade dentro do token budget │
│    ▼                                                                    │
│  RAG Retriever ── FAISS + sentence-transformers (384d)                 │
│    ▼                                                                    │
│  Orchestrator (State Machine)                                          │
│    IDLE → DETECTING → DIAGNOSING → MITIGATING → VERIFYING → RESOLVED  │
│    │                                                                    │
│    ├─ Diagnoser ── ReAct loop com Claude (tools: logs, metrics, traces)│
│    ├─ PreValidator ── blast radius + risk scoring                      │
│    ├─ VersionValidator ── CVE/version checks                           │
│    └─ TNR ── Transactional No-Regression (rollback se health cair)     │
│    ▼                                                                    │
│  Postmortem Generator → output/                                        │
│    ▼                                                                    │
│  Slack Notifier ── Block Kit progressivo (checklist → diagnostico)     │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### Pre-requisitos

- Python 3.12+
- Docker e Docker Compose v2 (para testes com infraestrutura local)
- (Opcional) `ANTHROPIC_API_KEY` para modo LLM

### 1. Clonar e instalar

```bash
git clone https://github.com/allanbrunobr/aiops-agent.git
cd aiops-agent

python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configurar variaveis de ambiente

```bash
cp env.example .env.local
```

Edite `.env.local` e preencha:

```env
# Para o pipeline principal (modo LLM)
ANTHROPIC_API_KEY=sk-ant-...

# Para o webhook server + notificacoes Slack
GRAFANA_WEBHOOK_TOKEN=test-grafana-token-local
SLACK_BOT_TOKEN=xoxb-seu-token-aqui
SLACK_CHANNEL=#aiops-alerts
```

### 3. Executar o pipeline principal

```bash
# Pipeline completo com LLM (requer ANTHROPIC_API_KEY)
python main.py

# Modo heuristico — sem chamadas ao Claude, sem custos
python main.py --no-llm

# Sem indexacao FAISS — startup mais rapido
python main.py --no-rag

# Output compacto
python main.py --compact
```

O `main.py` usa dados simulados de `data/sample_incident.py` (cenario: payment-svc
com connection pool exhaustion apos deploy ruim). Output via `rich` no terminal.

---

## Teste End-to-End com Infraestrutura Local

Alem do `main.py`, o projeto inclui uma infraestrutura Docker completa em `infra/`
que simula o ciclo real: **metricas → Prometheus → Grafana → webhook → pipeline → Slack**.

### Subir o ambiente

```bash
# 1. Subir Grafana + Prometheus + Metrics Exporter + Slack Mock
docker compose -f infra/docker-compose.yaml up -d --build

# 2. Iniciar o webhook server do AIOps Agent
python infra/run_webhook_server.py
```

### O que acontece

O **Metrics Exporter** simula um incidente no `payment-svc` em ciclo automatico:

| Tempo       | Fase        | Metricas                                  |
|-------------|-------------|-------------------------------------------|
| 0 — 2 min   | Normal      | error_rate ~1%, pool ~30%, latency ~45ms |
| 2 — 2.5 min | Degradacao  | metricas sobem gradualmente              |
| 2.5 — 5 min | Incidente   | error_rate 38%, pool 100%, latency 950ms |
| 5+ min      | Recuperacao | retorno ao normal                         |

O **Grafana** avalia 3 regras de alerta provisionadas automaticamente:
- High Error Rate > 10% → critical
- Connection Pool > 80% → critical
- Latency P95 > 500ms → warning

Quando dispara, envia **webhook POST** para o AIOps Agent, que normaliza,
deduplica e processa. O **SlackNotifier** envia notificacao Block Kit progressiva
no canal `#aiops-alerts` com diagnostico, evidencias e proposta de fix.

### Testes manuais

```bash
# Simular webhook do Grafana (sem esperar ciclo)
bash infra/test-webhook.sh

# Forcar incidente imediato no exporter
curl -X POST http://localhost:9101/trigger-incident

# Teste completo do Slack (13 chamadas: post + 11 updates + final)
python infra/test_slack_notification.py
```

### Servicos Docker

| Servico          | URL                          | Funcao                         |
|------------------|------------------------------|--------------------------------|
| Prometheus       | http://localhost:9090         | Armazena metricas              |
| Grafana          | http://localhost:3001         | Dashboard + alertas (admin/aiops123) |
| Metrics Exporter | http://localhost:9101/metrics | Simula payment-svc             |
| Slack Mock       | http://localhost:8003         | Mock da API Slack (opcional)   |

> **Documentacao completa da infraestrutura:** [`infra/README.md`](infra/README.md)

### Parar tudo

```bash
docker compose -f infra/docker-compose.yaml down -v
```

---

## Configurar Slack (workspace gratuito)

Para receber notificacoes reais no Slack:

1. Crie um workspace gratuito em https://slack.com/get-started#/createnew
2. Crie o canal `#aiops-alerts`
3. Crie um Slack App em https://api.slack.com/apps (**From scratch**)
4. Em **OAuth & Permissions**, adicione scopes: `chat:write`, `chat:write.public`
5. **Install to Workspace** e copie o **Bot User OAuth Token** (`xoxb-...`)
6. Cole o token no `SLACK_BOT_TOKEN` em `.env.local`

Guia detalhado: [`infra/SLACK-SETUP.md`](infra/SLACK-SETUP.md)

---

## Arquitetura do Projeto

```
aiops-agent/
├── main.py                 # Ponto de entrada do pipeline principal
├── env.example             # Template de variaveis de ambiente
│
├── agents/                 # Agentes IA (orchestrator, diagnoser, detector, mitigator)
├── blueprint/              # Blueprint Engine — orquestracao como typed graph
├── pipeline/               # Estagios de processamento (sanitizer, aggregator, compressor)
├── rag/                    # Knowledge base com FAISS + sentence-transformers
├── safety/                 # Camadas de seguranca (pre_validator, tnr, version_validator)
│
├── server/                 # FastAPI webhook server
│   ├── webhook/            # Router, normalizers (Grafana/PagerDuty/Databricks), dedup
│   └── notifications/      # SlackNotifier (Block Kit), fallback local
│
├── mcp_server/             # MCP server para agentes IA externos
├── core/                   # Protocolos e modelos compartilhados
├── config/                 # Configuracao YAML + loader
├── data/                   # Dados simulados do incidente
├── tools/                  # Interfaces abstratas (MetricsSource, LogSource)
│
├── infra/                  # Infraestrutura local de teste (Docker)
│   ├── docker-compose.yaml # Prometheus + Grafana + Exporter + Slack Mock
│   ├── grafana/            # Provisioning completo (alertas, dashboard, datasource)
│   ├── metrics-exporter/   # Simulador de metricas do payment-svc
│   ├── slack-mock/         # Mock server da API Slack
│   └── README.md           # Guia completo da infraestrutura
│
├── tests/                  # Suite de testes (unit + integration)
├── docs/                   # Documentacao tecnica e apresentacao
└── output/                 # Postmortems gerados e fallbacks
```

---

## Modulos Principais

| Modulo | Descricao | Paper base |
|--------|-----------|------------|
| `agents/orchestrator.py` | Maquina de estados STRATUS (IDLE → RESOLVED) com rollback | STRATUS |
| `agents/diagnoser.py` | Loop ReAct com Claude — tools: logs, metrics, traces, search | OpsAgent |
| `pipeline/telemetry_sanitizer.py` | Detecta payloads adversariais (reward-hacking) | AIOpsShield |
| `pipeline/aggregator.py` | Drain3 (logs) + 3-sigma (metricas) + p95 (traces) | RCACopilot |
| `pipeline/context_compressor.py` | Compressao por prioridade dentro do token budget | Acon² + AOI |
| `rag/indexer.py` | FAISS IndexFlatIP com all-MiniLM-L6-v2 (384d) | TAMO |
| `safety/pre_validator.py` | Blast radius scoring + blocked commands | AIOpsShield |
| `safety/tnr.py` | Transactional No-Regression — rollback se health cair | STRATUS |
| `safety/version_validator.py` | CVE/version checks nas remediacoes propostas | AIOpsShield |
| `server/webhook/router.py` | Pipeline de 8 estagios: size → parse → source → auth → norm → dedup → queue → accept | — |
| `server/notifications/slack.py` | Block Kit progressivo com retry exponencial e fallback | — |

---

## Testes

```bash
# Todos os testes
python -m pytest tests/ -v

# Apenas unitarios
python -m pytest tests/ -v -m unit

# Integracao (webhook → Slack)
python -m pytest tests/integration/ -v
```

---

## Papers Academicos

Este projeto implementa tecnicas de 7 papers de AIOps (2024-2025):

1. **STRATUS** — State machine para orquestracao de incidentes com rollback
2. **OpsAgent** — Loop ReAct para diagnostico iterativo com ferramentas
3. **RCACopilot** — Correlacao multi-sinal (logs + metricas + traces)
4. **Acon²** — Compressao de contexto por prioridade
5. **AOI** — Token budget allocation para observabilidade
6. **TAMO** — RAG com embeddings para busca em incidentes historicos
7. **AIOpsShield** — Deteccao de payloads adversariais e reward-hacking

---

## Licenca

Projeto academico / proof-of-concept.
