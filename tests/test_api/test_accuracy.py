from __future__ import annotations

"""
Testes para Story 3.3: Diagnostic Accuracy Tracking.
Cobre endpoints PATCH /accuracy e GET /metrics/accuracy.
"""

import dataclasses
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from adapters.persistence.in_memory_store import InMemoryStore
from domain.alert import NormalizedAlert
from domain.incident_state import IncidentState
from interfaces.http.api.accuracy import router, get_store
from fastapi import FastAPI
import datetime


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_alert(cid: str) -> NormalizedAlert:
    """Cria NormalizedAlert mínimo para IncidentState."""
    now = datetime.datetime(2026, 3, 4, 14, 0, 0)
    return NormalizedAlert(
        source="test",
        source_id=cid,
        correlation_id=cid,
        title="Test alert",
        description="Test",
        severity="error",
        alert_type="test",
        timestamp=now,
        detection_timestamp=now,
    )


def _make_incident(cid: str, state: str = "RESOLVED") -> IncidentState:
    """Cria IncidentState mínimo com accuracy_status=pending_review."""
    now = datetime.datetime(2026, 3, 4, 14, 0, 0)
    return IncidentState(
        incident_id=cid,
        correlation_id=cid,
        alert=_make_alert(cid),
        received_at=now,
        started_at=now,
        state=state,
        diagnosis={"confidence": 0.8, "root_cause": "connection pool exhausted"},
    )


def _make_app(store: InMemoryStore) -> FastAPI:
    """Cria FastAPI app com router de acurácia e store injetado."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = lambda: store
    return app


# ── testes ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_incident_default_accuracy_status_is_pending_review():
    """6.1: IncidentState criado com accuracy_status=pending_review."""
    incident = _make_incident("corr-001")
    assert incident.accuracy_status == "pending_review"
    assert incident.accuracy_verdict is None
    assert incident.reviewed_at is None


@pytest.mark.asyncio
async def test_patch_accuracy_updates_verdict_and_notes():
    """6.2: PATCH atualiza accuracy_verdict, accuracy_notes, reviewed_at."""
    store = InMemoryStore()
    store.save("corr-001", _make_incident("corr-001"))

    app = _make_app(store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/incidents/corr-001/accuracy",
            json={"accuracy": "correct", "notes": "Looks good"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["accuracy_verdict"] == "correct"
    assert data["correlation_id"] == "corr-001"
    assert "reviewed_at" in data

    # Verifica que store foi atualizado
    updated = store.get("corr-001")
    assert updated is not None
    assert updated.accuracy_verdict == "correct"
    assert updated.accuracy_notes == "Looks good"
    assert updated.accuracy_status == "reviewed"
    assert updated.reviewed_at is not None


@pytest.mark.asyncio
async def test_patch_invalid_accuracy_returns_422():
    """6.3: PATCH com categoria inválida retorna 422."""
    store = InMemoryStore()
    store.save("corr-001", _make_incident("corr-001"))

    app = _make_app(store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/incidents/corr-001/accuracy",
            json={"accuracy": "wrong_value"},
        )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_patch_unknown_correlation_id_returns_404():
    """6.4: PATCH em correlation_id inexistente retorna 404."""
    store = InMemoryStore()

    app = _make_app(store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.patch(
            "/api/v1/incidents/does-not-exist/accuracy",
            json={"accuracy": "correct"},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_accuracy_metrics_aggregates_correctly():
    """6.5: GET /metrics/accuracy retorna métricas agregadas corretas."""
    store = InMemoryStore()

    # 3 incidentes: 2 reviewed (1 correct, 1 false_positive), 1 pending
    inc1 = dataclasses.replace(
        _make_incident("c1"),
        accuracy_status="reviewed",
        accuracy_verdict="correct",
        reviewed_at=datetime.datetime(2026, 3, 4, 15, 0, 0),
    )
    inc2 = dataclasses.replace(
        _make_incident("c2"),
        accuracy_status="reviewed",
        accuracy_verdict="false_positive",
        reviewed_at=datetime.datetime(2026, 3, 4, 16, 0, 0),
    )
    inc3 = _make_incident("c3")  # pending_review

    for cid, inc in [("c1", inc1), ("c2", inc2), ("c3", inc3)]:
        store.save(cid, inc)

    app = _make_app(store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/metrics/accuracy")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_incidents"] == 3
    assert data["total_reviewed"] == 2
    assert data["total_pending"] == 1
    assert data["accuracy_rate"] == 0.5       # 1 correct / 2 reviewed
    assert data["false_positive_rate"] == 0.5  # 1 fp / 2 reviewed
    assert data["category_breakdown"]["correct"] == 1
    assert data["category_breakdown"]["false_positive"] == 1
    assert data["category_breakdown"]["incorrect"] == 0


@pytest.mark.asyncio
async def test_get_accuracy_metrics_empty_store_returns_zeroed():
    """6.6: GET accuracy com store vazio retorna métricas zeradas."""
    store = InMemoryStore()

    app = _make_app(store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/metrics/accuracy")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_incidents"] == 0
    assert data["total_reviewed"] == 0
    assert data["accuracy_rate"] == 0.0
    assert data["false_positive_rate"] == 0.0


@pytest.mark.asyncio
async def test_patch_overwrites_previous_review():
    """6.7: PATCH sobrescreve review anterior."""
    store = InMemoryStore()
    store.save("corr-001", _make_incident("corr-001"))

    app = _make_app(store)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Primeira review
        await client.patch(
            "/api/v1/incidents/corr-001/accuracy",
            json={"accuracy": "correct", "notes": "First review"},
        )
        # Segunda review (overwrites)
        resp = await client.patch(
            "/api/v1/incidents/corr-001/accuracy",
            json={"accuracy": "incorrect", "notes": "Changed mind"},
        )

    assert resp.status_code == 200
    assert resp.json()["accuracy_verdict"] == "incorrect"

    updated = store.get("corr-001")
    assert updated is not None
    assert updated.accuracy_verdict == "incorrect"
    assert updated.accuracy_notes == "Changed mind"
