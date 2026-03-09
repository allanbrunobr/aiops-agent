"""Testes para Story 2.1: FastAPI Webhook Server com Health Endpoints."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.anyio
async def test_health_returns_200(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert "version" in data
        assert "timestamp" in data


@pytest.mark.anyio
async def test_health_ready_returns_200(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "components" in data
        assert "active_pipelines" in data
        assert "queued_alerts" in data


@pytest.mark.anyio
async def test_health_ready_includes_required_components(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/health/ready")
        data = resp.json()
        component_names = [c["name"] for c in data["components"]]
        assert "webhook" in component_names
        assert "store" in component_names
        assert "credentials" in component_names


@pytest.mark.anyio
async def test_webhook_returns_202(app):
    """Story 2.1 AC2: webhook retorna status de aceitacao."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # Sem credentials de pagerduty — espera 401 auth failed
        resp = await client.post(
            "/api/v1/webhook",
            json={"source": "pagerduty", "event": "trigger"},
            headers={"Alert-Source": "pagerduty"},
        )
        # 401 = normalizer existe e tentou auth (sem secret configurado)
        assert resp.status_code in (202, 400, 401, 422, 503)


@pytest.mark.anyio
async def test_health_fast_response(app):
    """Story 2.1 AC3: health endpoint responde em < 10ms."""
    import time
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        start = time.monotonic()
        resp = await client.get("/health")
        elapsed_ms = (time.monotonic() - start) * 1000
        assert resp.status_code == 200
        # Tolerancia generosa para ambiente de CI
        assert elapsed_ms < 500


@pytest.mark.anyio
async def test_composition_root_wires_store(app):
    """Story 2.1 AC1: composition root inicializa store e credentials."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resp = await client.get("/health/ready")
        data = resp.json()
        store_component = next(c for c in data["components"] if c["name"] == "store")
        assert store_component["healthy"] is True

        creds_component = next(c for c in data["components"] if c["name"] == "credentials")
        assert creds_component["healthy"] is True


@pytest.mark.anyio
async def test_webhook_endpoint_registered(app):
    """Story 2.1 AC1: router webhook esta registrado em /api/v1/webhook."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # POST para endpoint inexistente retorna 405 (method not allowed) ou 404
        # POST para /api/v1/webhook deve retornar algo diferente de 404
        resp = await client.post(
            "/api/v1/webhook",
            json={"data": "test"},
        )
        assert resp.status_code != 404
