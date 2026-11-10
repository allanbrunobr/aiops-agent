"""Router do webhook — a porta de entrada externa, que não tinha nenhum teste.

P2 — T-c, 4B-01, 4B-08, 4B-09/SEC-10 (docs/analise-20261109.md):
- 4B-01: o fingerprint de dedup era gravado ANTES do submit; com a fila cheia
  (503) o retry do emissor era respondido como "deduplicated" e o alerta sumia;
- 4B-08: payload autenticado com tipo inesperado gerava 500 (só ValueError e
  KeyError eram tratados), e o emissor reenviava;
- 4B-09/SEC-10: sem Content-Length, o corpo era lido INTEIRO antes de checar o
  limite.
O token do Grafana é gerado em tempo de execução.
"""
from __future__ import annotations

import asyncio
import json
import secrets

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from interfaces.http.webhook import router as router_mod
from interfaces.http.webhook.dedup import DedupStore
from interfaces.http.webhook.errors import WebhookError, webhook_error_handler
from interfaces.http.webhook.normalizers import grafana as _grafana  # noqa: F401 — registra o normalizador
from interfaces.http.webhook.router import webhook_router

TOKEN = secrets.token_urlsafe(24)
PAYLOAD = {
    "status": "firing",
    "alerts": [{
        "status": "firing",
        "labels": {"alertname": "HighCPU", "severity": "critical", "service": "api"},
        "annotations": {"description": "CPU usage > 90%"},
        "startsAt": "2026-03-08T10:00:00Z",
        "fingerprint": "abc123def456",
    }],
}


class _Creds:
    def get(self, key):
        return TOKEN if key == "GRAFANA_WEBHOOK_TOKEN" else None


class _Runner:
    def __init__(self, answers):
        self.answers = list(answers)
        self.submitted = []

    async def submit(self, alert):
        accepted = self.answers.pop(0) if self.answers else True
        if accepted:
            self.submitted.append(alert)
        return accepted


def _app(runner=None) -> FastAPI:
    app = FastAPI()
    app.include_router(webhook_router)
    app.add_exception_handler(WebhookError, webhook_error_handler)
    app.state.credentials = _Creds()
    app.state.dedup_store = DedupStore()
    app.state.pipeline_runner = runner or _Runner([])
    return app


def _post(client, payload=PAYLOAD, token=TOKEN, **headers):
    h = {"Alert-Source": "grafana", "Content-Type": "application/json", **headers}
    if token is not None:
        h["Authorization"] = f"Bearer {token}"
    body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    return client.post("/webhook", content=body, headers=h)


@pytest.mark.unit
class TestWebhookRouter:
    def test_valid_alert_is_accepted_and_submitted(self):
        runner = _Runner([True])
        resp = _post(TestClient(_app(runner)))
        assert resp.status_code == 202
        assert len(runner.submitted) == 1

    @pytest.mark.parametrize("token", [None, "wrong-token"])
    def test_missing_or_wrong_token_is_401(self, token):
        runner = _Runner([True])
        resp = _post(TestClient(_app(runner)), token=token)
        assert resp.status_code == 401
        assert runner.submitted == []

    def test_declared_oversized_payload_is_413(self):
        resp = TestClient(_app()).post(
            "/webhook", content=b"{}",
            headers={"Content-Length": str(router_mod.DEFAULT_MAX_PAYLOAD_BYTES + 1),
                     "Alert-Source": "grafana"},
        )
        assert resp.status_code == 413

    def test_invalid_json_is_422_and_non_object_is_400(self):
        client = TestClient(_app())
        assert _post(client, payload=b"{not json").status_code == 422
        assert _post(client, payload=b"[1, 2]").status_code == 400

    def test_unexpected_payload_type_is_422_not_500(self):
        """4B-08: normalizador levantando TypeError/AttributeError."""
        client = TestClient(_app(), raise_server_exceptions=False)
        resp = _post(client, payload={"status": "firing", "alerts": [1]})
        assert resp.status_code == 422

    def test_retry_after_queue_full_is_not_swallowed_as_duplicate(self):
        """4B-01: 503 por fila cheia, depois o reenvio do emissor tem de ser aceito."""
        runner = _Runner([False, True])
        client = TestClient(_app(runner))
        first = _post(client)
        assert first.status_code == 503
        retry = _post(client)
        assert retry.status_code == 202
        assert retry.json().get("deduplicated") is not True
        assert len(runner.submitted) == 1

    def test_duplicate_after_success_is_deduplicated(self):
        runner = _Runner([True, True])
        client = TestClient(_app(runner))
        assert _post(client).status_code == 202
        second = _post(client)
        assert second.json().get("deduplicated") is True
        assert len(runner.submitted) == 1


@pytest.mark.unit
class TestBoundedBodyRead:
    def test_body_without_content_length_is_not_read_past_the_limit(self):
        """4B-09/SEC-10: corpo em pedaços, sem Content-Length."""
        chunk = b"x" * 65_536
        total_chunks = (router_mod.DEFAULT_MAX_PAYLOAD_BYTES // len(chunk)) * 4  # ~4 MiB
        consumed = 0
        sent = {}

        async def receive():
            nonlocal consumed
            consumed += 1
            return {"type": "http.request", "body": chunk,
                    "more_body": consumed < total_chunks}

        async def send(message):
            if message["type"] == "http.response.start":
                sent["status"] = message["status"]

        scope = {
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "POST", "scheme": "http", "path": "/webhook", "raw_path": b"/webhook",
            "query_string": b"", "root_path": "",
            "headers": [(b"alert-source", b"grafana"), (b"content-type", b"application/json")],
            "client": ("test", 1), "server": ("test", 80),
        }
        asyncio.run(_app()(scope, receive, send))

        assert sent["status"] == 413
        limit_chunks = router_mod.DEFAULT_MAX_PAYLOAD_BYTES // len(chunk)
        assert consumed <= limit_chunks + 2, f"leu {consumed} de {total_chunks} pedaços"
