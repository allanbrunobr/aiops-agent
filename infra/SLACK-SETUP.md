# Test Slack Workspace — Setup

## 1. Create a Free Workspace

1. Go to https://slack.com/get-started#/createnew
2. Use a personal email address
3. Suggested name: `AIOps Lab` or `aiops-test`
4. Create the channel `#aiops-alerts`

## 2. Create a Slack App

1. Go to https://api.slack.com/apps
2. Click **Create New App** → **From scratch**
3. Name: `AIOps Agent Bot`
4. Workspace: select the workspace you created above

## 3. Configure the Bot Token

1. In the app panel, go to **OAuth & Permissions**
2. Under **Bot Token Scopes**, add:
   - `chat:write` — post messages
   - `chat:write.public` — post to public channels without being a member
3. Click **Install to Workspace** at the top of the page
4. Authorize
5. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

## 4. Configure the AIOps Agent

Edit `.env.local` at the project root:

```
SLACK_BOT_TOKEN=xoxb-your-token-here
SLACK_CHANNEL=#aiops-alerts
```

## 5. Test

```bash
# Quick test with curl
curl -X POST https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer xoxb-your-token-here" \
  -H "Content-Type: application/json" \
  -d '{"channel": "#aiops-alerts", "text": "AIOps Agent connected!"}'
```

## 6. Full Test Flow

```
Metrics Exporter (Docker)
    ↓ scrape every 15s
Prometheus (Docker)
    ↓ datasource
Grafana (Docker) — detects anomaly
    ↓ webhook POST (Bearer token)
AIOps Agent (local :47890)
    ↓ normalize → pipeline → diagnosis
Slack (cloud) — Block Kit notification
```

## Alternative: Dry-Run (no real Slack)

If you want to test without Slack:

```python
# SlackNotifier supports dry_run=True
# Logs show the Block Kit blocks that would be sent
notifier = SlackNotifier(channel="#test", creds=creds, dry_run=True)
```

The blocks are logged as JSON via structlog — useful to validate the format.
