# Local Testing Environment Setup

Complete guide for setting up Grafana + Prometheus + Slack mock for aiops-agent integration testing.

## Architecture

```
+-------------------+       +-------------------+       +-------------------+
| metrics-simulator |------>|    Prometheus      |------>|     Grafana       |
| (Python, :9091)   | scrape| (:9090)            | query| (:3000)           |
+-------------------+       +-------------------+       +--------+----------+
                                                                  |
                                                         webhook POST
                                                                  |
                                                         +--------v----------+
                                                         |   aiops-agent     |
                                                         |   FastAPI (:8000) |
                                                         +--------+----------+
                                                                  |
                                                         Slack API POST
                                                                  |
                                                         +--------v----------+
                                                         | Slack Mock (:8001)|
                                                         | or Real Slack     |
                                                         +-------------------+
```

## Quick Start

### 1. Start the Docker stack

```bash
docker compose -f infra/docker-compose.yaml up -d --build
```

Verify services:
- Prometheus: http://localhost:9090 (check Targets page for metrics-simulator)
- Grafana: http://localhost:3001 (admin / aiops123)
- Metrics Exporter: http://localhost:9101/metrics

### 2. Start the Slack mock server

```bash
cd /Users/bruno/0\ -\ AI\ projects/aiops-agent
source .venv/bin/activate
python infra/slack-mock/slack_mock_server.py
```

Verify: http://localhost:8003/_mock/health

### 3. Start the aiops-agent webhook server

```bash
# In a separate terminal
source .venv/bin/activate

# Set Slack to use mock server
export SLACK_API_BASE_URL=http://localhost:8001/api/
export SLACK_BOT_TOKEN=xoxb-mock-token-for-testing
export SLACK_CHANNEL=C_TEST_CHANNEL

python -m uvicorn interfaces.http.webhook:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Watch it work

The metrics simulator cycles every 120 seconds:
- First 80s: normal values (no alerts)
- Next 40s: spike values (alerts fire)

Monitor:
- Grafana alerting: http://localhost:3001/alerting/list
- Slack mock messages: http://localhost:8003/_mock/messages
- Prometheus metrics: http://localhost:9090/graph?g0.expr=payment_svc_error_rate

### 5. Manual trigger for immediate testing

```bash
# Send the example payload directly (no need to wait for spike cycle)
bash infra/test-webhook.sh
```

---

## Detailed Configuration

### Docker Compose Services

| Service            | Port | Purpose                                    |
|--------------------|------|--------------------------------------------|
| prometheus         | 9090 | Scrapes and stores metrics                 |
| grafana            | 3001 | Visualizes metrics, fires alerts           |
| metrics-exporter   | 9101 | Generates metrics with spike patterns      |
| slack-mock         | 8003 | Mock Slack API (optional)                  |

### Grafana Provisioning Files

All in `infra/grafana/provisioning/`:

| File                          | Purpose                                      |
|-------------------------------|----------------------------------------------|
| `datasources/prometheus.yaml`     | Configures Prometheus as default datasource  |
| `alerting/contact-points.yaml`    | Webhook contact point -> host.docker.internal|
| `alerting/notification-policies.yaml` | Routes all alerts to the webhook         |
| `alerting/alert-rules.yaml`       | 3 alert rules (error rate, latency, pool)    |

### Alert Rules

| Rule                    | Metric                        | Threshold | Severity |
|-------------------------|-------------------------------|-----------|----------|
| High Error Rate         | payment_svc_error_rate        | > 0.05    | critical |
| High P95 Latency        | payment_svc_latency_p95       | > 500ms   | warning  |
| Connection Pool Exhaust | payment_svc_pool_active_ratio | > 0.9     | critical |

### Grafana Webhook Payload Format

See Grafana Unified Alerting documentation for the complete payload format.

Key fields your normalizer should extract:

```python
# From the webhook POST body:
payload["status"]                          # "firing" or "resolved"
payload["alerts"][0]["labels"]["service"]   # "payment-svc"
payload["alerts"][0]["labels"]["severity"]  # "critical"
payload["alerts"][0]["annotations"]["summary"]  # Human-readable summary
payload["alerts"][0]["values"]             # {"error_rate": 0.1523, ...}
payload["alerts"][0]["startsAt"]           # RFC3339 timestamp
payload["alerts"][0]["fingerprint"]        # Unique alert identifier
```

### Metrics Simulator Behavior

The simulator exposes 4 Prometheus gauges that cycle between normal and spike:

```
payment_svc_error_rate{service="payment-svc",environment="staging"}
payment_svc_latency_p95{service="payment-svc",environment="staging"}
payment_svc_pool_active_ratio{service="payment-svc",environment="staging"}
payment_svc_request_count{service="payment-svc",environment="staging"}
```

Cycle: 120s total. Normal for 80s, spike for 40s. Alerts need 30s sustained
spike (the `for:` duration in rules), so they fire ~10s into each spike phase.

---

## Slack Setup Options

### Option A: Mock Server (Recommended for development)

The mock server at `infra/slack-mock/slack_mock_server.py`:
- Accepts standard Slack Web API calls (`chat.postMessage`, `chat.update`, `auth.test`)
- Stores all messages in memory
- Provides `/_mock/messages` endpoint for test assertions
- No Slack account needed

Configure your Slack SDK client:
```python
from slack_sdk import WebClient

client = WebClient(
    token="xoxb-mock-token",
    base_url="http://localhost:8001/api/"
)

# Works exactly like real Slack SDK calls
response = client.chat_postMessage(channel="#alerts", text="Test alert")
print(response["ts"])  # Returns a mock timestamp
```

### Option B: Free Slack Workspace (for realistic testing)

1. **Create workspace**: Go to https://slack.com/get-started#/createnew
   - Use any email, it is free for small teams
   - Create a `#aiops-alerts` channel

2. **Create a Slack App**:
   - Go to https://api.slack.com/apps
   - Click "Create New App" > "From scratch"
   - Name: `aiops-agent-dev`, pick your test workspace

3. **Configure OAuth scopes** (under "OAuth & Permissions"):
   - `chat:write` -- post messages to channels
   - `chat:write.customize` -- post with custom username/icon
   - `chat:write.public` -- post to channels without joining

   Note: `chat:update` is not a separate scope. The `chat:write` scope
   covers updating messages that your bot posted.

4. **Install to workspace**:
   - Click "Install to Workspace" and authorize
   - Copy the "Bot User OAuth Token" (starts with `xoxb-`)

5. **Set environment variables**:
   ```bash
   export SLACK_BOT_TOKEN=xoxb-your-actual-token
   export SLACK_CHANNEL=C07XXXXXXXX   # channel ID from Slack
   # Do NOT set SLACK_API_BASE_URL (uses real Slack API)
   ```

### Option C: Hybrid (mock for CI, real for manual testing)

```bash
# .env.test (used by CI/pytest)
SLACK_API_BASE_URL=http://localhost:8001/api/
SLACK_BOT_TOKEN=xoxb-mock-token
SLACK_CHANNEL=C_TEST

# .env (used for manual testing with real Slack)
SLACK_BOT_TOKEN=xoxb-real-token-here
SLACK_CHANNEL=C07XXXXXXXX
```

---

## Running Integration Tests

### Automated (mock server, no external dependencies)

```bash
# Terminal 1: Start Slack mock
python infra/slack-mock/slack_mock_server.py

# Terminal 2: Start aiops-agent webhook server
python -m uvicorn interfaces.http.webhook:app --port 8000

# Terminal 3: Run tests
pytest tests/integration/test_webhook_to_slack_flow.py -v
```

### Full stack (with Grafana triggering alerts)

```bash
# Terminal 1: Docker stack
docker compose -f infra/docker-compose.yaml up -d --build

# Terminal 2: Slack mock
python infra/slack-mock/slack_mock_server.py

# Terminal 3: aiops-agent
python -m uvicorn interfaces.http.webhook:app --port 8000

# Wait ~90 seconds for the first spike cycle, then check:
curl http://localhost:8003/_mock/messages | python -m json.tool
```

---

## Troubleshooting

### host.docker.internal not resolving (Linux)

On Linux, `host.docker.internal` may not work by default. Add to docker-compose.yml:

```yaml
grafana:
  extra_hosts:
    - "host.docker.internal:host-gateway"
```

### Grafana alerts not firing

1. Check Prometheus targets: http://localhost:9090/targets (should show metrics-simulator as UP)
2. Check Grafana alert rules: http://localhost:3001/alerting/list (should show rules in "AIOps" folder)
3. Query directly: http://localhost:9090/graph?g0.expr=payment_svc_error_rate (should show data)
4. Check Grafana logs: `docker logs aiops-grafana`

### Provisioning files not loading

- Grafana only reads provisioning files at startup. After changes: `docker compose restart grafana`
- Check for YAML syntax errors in Grafana logs: `docker logs aiops-grafana 2>&1 | grep -i provision`

### Webhook not received

```bash
# Test connectivity from Grafana container to host
docker exec aiops-grafana wget -qO- http://host.docker.internal:8000/health
```

---

## Teardown

```bash
docker compose -f infra/docker-compose.yaml down -v
```
