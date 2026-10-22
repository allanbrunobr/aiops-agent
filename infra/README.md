# Infra — Local Test Environment for the AIOps Agent

This directory contains all the infrastructure you need to test the AIOps Agent
project **end-to-end** without depending on corporate environments. Everything
runs locally with Docker + Python.

---

## What this project does

It simulates the full cycle of a real incident:

```
Metrics Exporter  ──scrape 15s──>  Prometheus  ──query──>  Grafana
   (simulates                        (stores                (evaluates rules,
   payment-svc)                       metrics)               detects anomaly)
                                                                  │
                                                        webhook POST (Bearer)
                                                                  │
                                                                  v
                                                          AIOps Agent (:47890)
                                                          FastAPI webhook server
                                                                  │
                                                        normalize → dedup → pipeline
                                                                  │
                                                                  v
                                                           Slack (:cloud: real)
                                                           or Slack Mock (:47892)
                                                        progressive Block Kit
```

**Simulated scenario:** `payment-svc` suffers connection pool exhaustion after a
bad deploy (`max_connections` dropped from 200 to 50). The metrics exporter
generates metrics that reproduce this degradation. Grafana detects it and fires
a webhook. The AIOps Agent processes it and sends a Block Kit notification to
Slack with the diagnosis, evidence, confidence and a proposed fix.

---

## Prerequisites

- **Docker** and **Docker Compose** (v2)
- **Python 3.12+** with a venv
- (Optional) A free Slack workspace for real notifications

---

## Quick Start — 3 steps

### Step 1: Configure environment variables

Copy the template and edit it with your tokens:

```bash
# At the project root (not inside infra/)
cp env.example .env.local
```

Edit `.env.local`:

```env
# Token that Grafana sends in the header Authorization: Bearer <token>
# Must match the value set in infra/grafana/provisioning/alerting/contact-points.yaml
GRAFANA_WEBHOOK_TOKEN=test-grafana-token-local

# Slack Bot token (get it by following "Configure Slack" below)
# If you do not have one, leave it empty — the system works without Slack (local fallback)
SLACK_BOT_TOKEN=xoxb-your-token-here

# Slack channel where notifications are posted
SLACK_CHANNEL=#aiops-alerts
```

> **If you do not have a Slack token yet**, skip to the section
> [Configure Slack (free workspace)](#configure-slack-free-workspace)
> and then come back here.

### Step 2: Start the Docker infrastructure

```bash
docker compose -f infra/docker-compose.yaml up -d --build
```

Check that everything is up:

| Service          | URL                              | What it does                      |
|------------------|----------------------------------|-----------------------------------|
| Prometheus       | http://localhost:9090             | Stores the exporter metrics       |
| Grafana          | http://localhost:3001             | Dashboard + alerts (admin/aiops123) |
| Metrics Exporter | http://localhost:9101/metrics     | Simulates payment-svc metrics     |
| Slack Mock       | http://localhost:47892/_mock/health| Mock of the Slack API (optional)  |

### Step 3: Start the AIOps Agent webhook server

```bash
# At the project root
source .venv/bin/activate        # if not activated yet
pip install -r requirements.txt  # if not installed yet

python infra/run_webhook_server.py
```

Expected output:
```
============================================================
AIOps Agent — Webhook Server
============================================================
  Listening:    http://0.0.0.0:47890
  Health:       http://localhost:47890/health
  Webhook:      POST http://localhost:47890/webhook
  .env.local:   loaded
  GRAFANA_TOKEN: set
  SLACK_TOKEN:   set
============================================================
```

**Done!** Now just wait. The automatic cycle is:

| Time        | Phase       | What happens                                        |
|-------------|-------------|-----------------------------------------------------|
| 0 — 2 min   | Normal      | Stable metrics, no alerts                           |
| 2 — 2.5 min | Degrading   | Error rate and latency rise gradually               |
| 2.5 — 5 min | Incident    | Pool 100%, error_rate 38%, latency 950ms            |
| 5+ min      | Recovery    | Metrics return to normal                            |

When Grafana detects the anomalies (after ~2.5min), it fires a webhook
automatically to `http://host.docker.internal:47890/webhook`. You will see this
in the webhook server logs:

```
webhook.accepted  alert_severity=critical  alert_title=High Error Rate - payment-svc
```

> **Current limitation:** the webhook server `PipelineRunner` is still a stub. The
> alert is normalized, deduplicated and queued, and the log shows
> `pipeline.stub_executed`, but the webhook does not trigger the full diagnosis
> or the Slack notification. To validate the Slack Block Kit flow, use
> Option C (`infra/test_slack_notification.py`).

---

## Manual test (without waiting for the cycle)

### Option A — Simulate a Grafana webhook

```bash
bash infra/test-webhook.sh
```

Sends a payload identical to the one Grafana sends. You will see `HTTP Status: 202`.

### Option B — Force an incident in the exporter

```bash
# Force immediate degradation (without waiting 2 min)
curl -X POST http://localhost:9101/trigger-incident

# Return to normal
curl -X POST http://localhost:9101/reset
```

### Option C — Full Slack test (progressive Block Kit)

```bash
python infra/test_slack_notification.py
```

This script simulates the whole cycle that the pipeline runs:
1. `pipeline.started` → posts the initial message to Slack
2. `node.completed` x11 → updates the same message with a progressive checklist
3. `pipeline.completed` → updates it with the full diagnosis (root cause, evidence, fix)

Expected output:
```
[1/4] Sending pipeline.started to #aiops-alerts...
slack.message_posted  channel_id=C0AL9T4S8QJ  ts=1773347770.080689
[2/4] Node 1/11: telemetry_sanitizer (45ms)
[2/4] Node 2/11: log_parser (320ms)
...
[2/4] Node 11/11: postmortem_generator (380ms)
[3/4] Sending pipeline.completed with diagnosis...
slack.final_message  correlation_id=inc-20260312-demo0001
[4/4] Full notification sent!
```

---

## Configure Slack (free workspace)

To receive real notifications in Slack (and not only in the local mock):

### 1. Create a workspace

1. Go to https://slack.com/get-started#/createnew
2. Use any email address (it is free)
3. Suggested name: `AIOps Lab`
4. Create the channel `#aiops-alerts`

### 2. Create a Slack App (Bot)

1. Go to https://api.slack.com/apps
2. **Create New App** → **From scratch**
3. Name: `AIOps Agent Bot` | Workspace: the one you created
4. In the side menu, go to **OAuth & Permissions**
5. Under **Bot Token Scopes**, add:
   - `chat:write` — post messages
   - `chat:write.public` — post to public channels without being a member
6. Click **Install to Workspace** (top of the page) and authorize
7. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

### 3. Configure the project

Edit `.env.local` at the root:

```env
SLACK_BOT_TOKEN=xoxb-your-real-token-here
SLACK_CHANNEL=#aiops-alerts
```

### 4. Validate

```bash
# Quick test — should return {"ok": true, ...}
curl -s -X POST https://slack.com/api/auth.test \
  -H "Authorization: Bearer xoxb-your-real-token-here" | python -m json.tool
```

> **Technical note:** `chat.postMessage` accepts the channel name (`#aiops-alerts`),
> but `chat.update` requires the **channel ID** (e.g. `C0AL9T4S8QJ`). The
> `SlackNotifier` handles this automatically — it captures the ID from the
> first `postMessage` response and uses it in the following `update` calls.

---

## Alternative: use the Slack Mock (no real workspace)

If you do not want to create a Slack workspace, the Docker stack already includes
a mock server that simulates the Slack API:

```bash
# The mock is already running at http://localhost:47892
# Configure the SDK to point to it:
export SLACK_API_BASE_URL=http://localhost:47892/api/
export SLACK_BOT_TOKEN=xoxb-mock-token
```

View captured messages:
```bash
curl http://localhost:47892/_mock/messages | python -m json.tool
```

Clear messages:
```bash
curl -X DELETE http://localhost:47892/_mock/messages
```

---

## File structure

```
infra/
├── README.md                         ← you are here
├── docker-compose.yaml               ← orchestrates the 4 containers
├── setup.sh                          ← automated script (alternative to the manual quick start)
│
├── prometheus/
│   └── prometheus.yaml               ← config: scrapes metrics-exporter every 15s
│
├── grafana/provisioning/
│   ├── datasources/
│   │   └── prometheus.yaml           ← registers Prometheus as a datasource (uid: prometheus)
│   ├── alerting/
│   │   ├── alert-rules.yaml          ← 3 rules: error rate, pool exhaustion, p95 latency
│   │   ├── contact-points.yaml       ← webhook → http://host.docker.internal:47890/webhook
│   │   └── notification-policies.yaml← routes all alerts to the webhook
│   └── dashboards/
│       ├── dashboard-provider.yaml   ← auto-provisions dashboards from the json/ directory
│       └── json/payment-svc.json     ← dashboard with 5 payment-svc panels
│
├── metrics-exporter/
│   ├── Dockerfile                    ← python:3.12-slim
│   └── exporter.py                   ← HTTP server :9101, simulates the incident cycle
│
├── slack-mock/
│   ├── Dockerfile                    ← python:3.12-slim + fastapi + uvicorn
│   └── slack_mock_server.py          ← mock: chat.postMessage, chat.update, auth.test
│
├── run_webhook_server.py             ← boots the FastAPI app with the webhook router + deps
├── test-webhook.sh                   ← simulates a Grafana POST (curl)
├── test_slack_notification.py        ← full test of the Slack Block Kit cycle
└── SLACK-SETUP.md                    ← short Slack setup guide
```

---

## How it works internally

### Metrics Exporter (`metrics-exporter/exporter.py`)

A plain HTTP server (no frameworks) that exposes metrics in Prometheus format at
`GET /metrics`. It simulates 4 automatic phases of an incident in `payment-svc`:

| Metric                               | Normal    | Incident   |
|--------------------------------------|-----------|------------|
| `payment_svc_error_rate_percent`     | ~1%       | ~38%       |
| `payment_svc_connection_pool_active` | ~60/200   | 200/200    |
| `payment_svc_latency_ms` (p95)       | ~45ms     | ~950ms     |
| `payment_svc_requests_total`         | ~150 req/s| ~150 req/s |

Manual control:
- `POST /trigger-incident` — forces immediate degradation
- `POST /reset` — returns to the normal state

### Prometheus (`prometheus/prometheus.yaml`)

Simple scrape: collects metrics from the exporter every 15s with the label `service=payment-svc`.

### Grafana (full provisioning)

Everything is provisioned automatically at startup (no manual configuration):

- **Datasource:** Prometheus with `uid: prometheus` (critical — the alert rules reference this UID)
- **3 Alert Rules:**
  - High Error Rate > 10% for 30s → severity: critical
  - Connection Pool > 80% for 15s → severity: critical
  - Latency P95 > 500ms for 30s → severity: warning
- **Contact Point:** Webhook POST to `http://host.docker.internal:47890/webhook` with `Authorization: Bearer test-grafana-token-local`
- **Notification Policy:** Routes all alerts to the webhook contact point
- **Dashboard:** 5 panels (Error Rate, Connection Pool, Latency, CPU/Memory, Incident Phase)

### Webhook Server (`run_webhook_server.py`)

A FastAPI app that assembles the full pipeline:

1. Receives a POST at `/webhook`
2. Identifies the source (Grafana is detected by the `alerts` field in the payload)
3. Validates authentication (Bearer token vs `GRAFANA_WEBHOOK_TOKEN`)
4. Normalizes the payload (Grafana → `NormalizedAlert`)
5. Deduplicates by fingerprint (5min window)
6. Submits to the pipeline runner (max 3 concurrent, queue of 10)

### SlackNotifier (`interfaces/http/notifications/slack.py`)

Progressive Block Kit notifications:

1. `pipeline.started` → `chat.postMessage` with title, severity, service
2. `node.completed` x11 → `chat.update` with a progressive checklist (green checkmarks)
3. `pipeline.completed` → final `chat.update` with:
   - Root cause
   - Evidence
   - Confidence (with a color emoji)
   - Risk level and blast radius
   - Proposed fix (in a code block)

---

## Troubleshooting

### "Grafana alerts not firing"

1. Check that Prometheus is scraping: http://localhost:9090/targets
   (it should show `metrics-exporter:9101` as UP)
2. Check the rules: http://localhost:3001/alerting/list (admin/aiops123)
3. Force an incident: `curl -X POST http://localhost:9101/trigger-incident`
4. Grafana logs: `docker logs aiops-grafana 2>&1 | grep -i alert`

### "webhook.rejected — unknown_source"

The webhook server could not identify the source. Grafana is detected
automatically by the `alerts` field (array) in the payload. If you are testing
manually, include the header `Alert-Source: grafana`.

### "webhook.rejected — auth_failed"

The token in the header `Authorization: Bearer <token>` does not match
`GRAFANA_WEBHOOK_TOKEN` in `.env.local`. Both must be equal (default:
`test-grafana-token-local`).

### "chat.update channel_not_found" in Slack

This happened because `chat.update` requires the **channel ID** (e.g. `C0AL9T4S8QJ`),
not the channel name. It is already fixed — `SlackNotifier` now captures the
channel ID from the `chat.postMessage` response and reuses it in the updates.

### Port conflicts

If ports 9090, 3001, 9101 or 47892 are in use:

```bash
# Find out what is using the port
lsof -i :3001

# Change it in docker-compose.yaml (format host:container)
# E.g.: change Grafana from 3001:3000 to 3002:3000
```

### host.docker.internal does not resolve (Linux)

`docker-compose.yaml` already includes `extra_hosts: host.docker.internal:host-gateway`
for Linux. If it still does not work:

```bash
# Test connectivity from the Grafana container to the host
docker exec aiops-grafana wget -qO- http://host.docker.internal:47890/health
```

---

## Teardown

```bash
# Stop and remove containers + volumes
docker compose -f infra/docker-compose.yaml down -v

# Stop the webhook server
# Ctrl+C in the terminal where it is running
```

---

## Relation to the main project

This infrastructure **is not the AIOps Agent itself** — it is a test environment
that simulates the outside world (Grafana, Prometheus, metrics) to validate that
the agent pipeline works end-to-end.

The main project (`main.py`) runs the full pipeline with an LLM:

```bash
# Full pipeline with Claude (requires ANTHROPIC_API_KEY in .env)
python main.py

# Heuristic mode (no LLM, no API costs)
python main.py --no-llm

# Mode without RAG (faster startup)
python main.py --no-rag
```

The difference:

| Component            | `main.py` (main project)             | `infra/` (local test)               |
|----------------------|--------------------------------------|--------------------------------------|
| Input data           | Simulated in `data/sample_incident.py`| Generated by the Docker metrics exporter |
| Alert source         | Internal (heuristic detector)        | Grafana via HTTP webhook             |
| Pipeline             | Full (LLM + RAG + safety)            | Webhook → normalizer → dedup         |
| Notification         | `rich` output (terminal)             | Slack Block Kit (real or mock)       |
| Requires LLM         | Yes (or `--no-llm`)                  | No                                   |
