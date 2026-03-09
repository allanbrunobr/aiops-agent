"""Testes para Story 2.4: Webhook Authentication and Payload Validation."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.anyio
async def test_unknown_source_returns_400(app):
    """AC1: fonte desconhecida retorna 400 com ProblemDetail."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/webhook",
            json={"data": "no source info"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert data["title"] == "Bad Request"
        assert "type" in data
        assert "correlation_id" in data
        assert resp.headers.get("content-type", "").startswith("application/problem+json")


@pytest.mark.anyio
async def test_unsupported_source_returns_400(app):
    """AC1: fonte nao registrada retorna 400."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/webhook",
            json={"data": "test"},
            headers={"Alert-Source": "unknown_source_xyz"},
        )
        assert resp.status_code == 400
        data = resp.json()
        assert "type" in data


@pytest.mark.anyio
async def test_invalid_auth_returns_401(app):
    """AC2: auth invalida retorna 401 com ProblemDetail."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # PagerDuty com signature invalida (secret nao configurado em env)
        resp = await client.post(
            "/api/v1/webhook",
            json={"event": {"data": {"id": "P1", "title": "T", "urgency": "high"}}},
            headers={
                "Alert-Source": "pagerduty",
                "x-pagerduty-signature": "v1=invalidsignature",
            },
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["title"] == "Unauthorized"
        assert resp.headers.get("content-type", "").startswith("application/problem+json")


@pytest.mark.anyio
async def test_oversized_payload_returns_413(app):
    """AC3: payload acima do limite retorna 413."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # Cria payload > 1MB
        large_payload = {"data": "x" * (1_048_577)}
        resp = await client.post(
            "/api/v1/webhook",
            content=json.dumps(large_payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Alert-Source": "pagerduty",
            },
        )
        assert resp.status_code == 413


@pytest.mark.anyio
async def test_malformed_json_returns_422(app):
    """AC4: JSON invalido retorna 422 com ProblemDetail."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/webhook",
            content=b"not json {{{",
            headers={"Content-Type": "application/json", "Alert-Source": "pagerduty"},
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["title"] == "Unprocessable Entity"
        assert resp.headers.get("content-type", "").startswith("application/problem+json")


@pytest.mark.anyio
async def test_problem_detail_has_rfc7807_fields(app):
    """AC: ProblemDetail segue RFC 7807."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/webhook",
            json={"no_source": "here"},
        )
        assert resp.status_code in (400, 401, 422)
        data = resp.json()
        # Campos RFC 7807 obrigatorios
        assert "type" in data
        assert "title" in data
        assert "status" in data
        assert "detail" in data


@pytest.mark.anyio
async def test_valid_request_passes_all_checks_pagerduty(app, monkeypatch):
    """AC: requisicao valida com assinatura correta passa todas as verificacoes."""
    secret = "test-webhook-secret"
    monkeypatch.setenv("PAGERDUTY_WEBHOOK_SECRET", secret)

    # Recria credentials com o env var setado
    from core.credentials import EnvCredentialProvider
    app.state.credentials = EnvCredentialProvider()

    payload = {
        "event": {
            "event_type": "incident.triggered",
            "occurred_at": "2026-03-08T10:00:00Z",
            "data": {
                "id": "P123",
                "title": "High CPU",
                "urgency": "high",
                "service": {"summary": "payment-svc"},
            },
        }
    }
    body = json.dumps(payload).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.post(
            "/api/v1/webhook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "x-pagerduty-signature": f"v1={signature}",
            },
        )
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "accepted"
        assert "correlation_id" in data
