"""
Integration test: Grafana webhook -> aiops-agent -> Slack notification.

This test simulates the full flow without requiring Docker services running:
  1. Sends a Grafana-format webhook payload to the aiops-agent FastAPI endpoint
  2. Verifies the webhook is received and parsed correctly
  3. Verifies a Slack notification is sent to the mock server
  4. Verifies the Slack message content matches expectations

Prerequisites:
  - The Slack mock server must be running: python docker/slack-mock/slack_mock_server.py
  - The aiops-agent webhook server must be running

For fully isolated tests (no servers needed), use test_webhook_normalizer_unit.py instead.

Run:
  pytest tests/integration/test_webhook_to_slack_flow.py -v
"""

import json
import time
from pathlib import Path

import httpx
import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WEBHOOK_URL = "http://localhost:8000/webhook"
SLACK_MOCK_URL = "http://localhost:8001"

SAMPLE_PAYLOAD_PATH = Path(__file__).parent.parent.parent / "docker" / "grafana_webhook_payload_example.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _service_available(url: str) -> bool:
    """Check if a service is reachable."""
    try:
        resp = httpx.get(url, timeout=2.0)
        return resp.status_code < 500
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def grafana_payload() -> dict:
    """Load the example Grafana webhook payload."""
    return json.loads(SAMPLE_PAYLOAD_PATH.read_text())


@pytest.fixture
def slack_mock_client():
    """HTTP client for the Slack mock server."""
    client = httpx.Client(base_url=SLACK_MOCK_URL, timeout=5.0)
    # Clear any previous messages
    try:
        client.delete("/_mock/messages")
    except httpx.ConnectError:
        pass
    yield client
    client.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWebhookToSlackFlow:
    """End-to-end integration test for the alert pipeline."""

    @pytest.mark.skipif(
        not _service_available(WEBHOOK_URL.replace("/webhook", "/health")),
        reason="aiops-agent webhook server not running",
    )
    @pytest.mark.skipif(
        not _service_available(f"{SLACK_MOCK_URL}/_mock/health"),
        reason="Slack mock server not running",
    )
    def test_firing_alert_produces_slack_message(
        self, grafana_payload: dict, slack_mock_client: httpx.Client
    ):
        """A firing Grafana alert should result in a Slack message."""
        # 1. Send webhook to aiops-agent
        response = httpx.post(WEBHOOK_URL, json=grafana_payload, timeout=10.0)
        assert response.status_code == 200, f"Webhook rejected: {response.text}"

        # 2. Give the agent a moment to process and post to Slack
        time.sleep(2)

        # 3. Check Slack mock for captured messages
        mock_resp = slack_mock_client.get("/_mock/messages")
        assert mock_resp.status_code == 200
        data = mock_resp.json()

        assert data["count"] >= 1, "No Slack messages were sent"

        # 4. Verify message content
        msg = data["messages"][0]
        assert "payment-svc" in msg["text"].lower() or (
            msg["blocks"] and "payment-svc" in json.dumps(msg["blocks"]).lower()
        ), "Slack message should reference payment-svc"

    @pytest.mark.skipif(
        not _service_available(WEBHOOK_URL.replace("/webhook", "/health")),
        reason="aiops-agent webhook server not running",
    )
    @pytest.mark.skipif(
        not _service_available(f"{SLACK_MOCK_URL}/_mock/health"),
        reason="Slack mock server not running",
    )
    def test_resolved_alert_updates_slack(
        self, grafana_payload: dict, slack_mock_client: httpx.Client
    ):
        """A resolved alert should update or send a follow-up Slack message."""
        # Send firing first
        httpx.post(WEBHOOK_URL, json=grafana_payload, timeout=10.0)
        time.sleep(1)

        # Now send resolved
        grafana_payload["status"] = "resolved"
        grafana_payload["state"] = "ok"
        for alert in grafana_payload["alerts"]:
            alert["status"] = "resolved"
            alert["endsAt"] = "2026-03-12T14:35:00.000Z"

        response = httpx.post(WEBHOOK_URL, json=grafana_payload, timeout=10.0)
        assert response.status_code == 200

        time.sleep(2)

        mock_resp = slack_mock_client.get("/_mock/messages")
        data = mock_resp.json()
        assert data["count"] >= 2, "Expected at least 2 Slack messages (firing + resolved)"


class TestWebhookPayloadValidation:
    """Validate the normalizer handles various Grafana payload shapes."""

    def test_payload_has_required_fields(self, grafana_payload: dict):
        """Verify our example payload has all expected Grafana fields."""
        assert "receiver" in grafana_payload
        assert "status" in grafana_payload
        assert "alerts" in grafana_payload
        assert isinstance(grafana_payload["alerts"], list)
        assert len(grafana_payload["alerts"]) > 0

        alert = grafana_payload["alerts"][0]
        assert "status" in alert
        assert "labels" in alert
        assert "annotations" in alert
        assert "startsAt" in alert
        assert "fingerprint" in alert
        assert "values" in alert

    def test_payload_labels_contain_service(self, grafana_payload: dict):
        """Alert labels should contain the service identifier."""
        alert = grafana_payload["alerts"][0]
        assert "service" in alert["labels"]
        assert alert["labels"]["service"] == "payment-svc"

    def test_payload_values_contain_metric(self, grafana_payload: dict):
        """Alert values should contain the metric that triggered."""
        alert = grafana_payload["alerts"][0]
        assert "error_rate" in alert["values"]
        assert isinstance(alert["values"]["error_rate"], (int, float))
