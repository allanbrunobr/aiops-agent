from __future__ import annotations

"""
Testes para Story 4.3: Add Incident Patterns to Knowledge Base.
Cobre POST /api/v1/knowledge/patterns e GET /api/v1/knowledge/patterns.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

from rag.knowledge_store import KnowledgeStore
from server.routes.knowledge import router, get_knowledge_store


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_app(store: KnowledgeStore) -> FastAPI:
    """Cria FastAPI app com router de knowledge e store injetado."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_knowledge_store] = lambda: store
    return app


_VALID_PAYLOAD = {
    "service": "payment-svc",
    "symptoms": ["Connection timeout", "High error rate"],
    "root_cause": "Connection pool exhausted after config change",
    "resolution": "Revert max_connections to 200",
    "tags": ["connection-pool", "config-change"],
    "confidence": 0.9,
}


# ── POST /api/v1/knowledge/patterns ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_pattern_returns_201(tmp_path):
    """POST com payload válido deve retornar 201."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    app = _make_app(store)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/knowledge/patterns", json=_VALID_PAYLOAD)

    assert resp.status_code == 201
    data = resp.json()
    assert "pattern_id" in data
    assert data["status"] == "created"
    assert data["duplicate_warning"] is None


@pytest.mark.asyncio
async def test_create_pattern_persists_to_store(tmp_path):
    """POST deve adicionar padrão ao KnowledgeStore."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    app = _make_app(store)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/knowledge/patterns", json=_VALID_PAYLOAD)

    patterns = store.get_all_patterns()
    assert len(patterns) == 1
    assert patterns[0].service == "payment-svc"


@pytest.mark.asyncio
async def test_create_pattern_marks_index_stale(tmp_path):
    """POST deve marcar o índice FAISS como stale."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    app = _make_app(store)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/knowledge/patterns", json=_VALID_PAYLOAD)

    assert store.is_index_stale


@pytest.mark.asyncio
async def test_create_duplicate_pattern_returns_warning(tmp_path):
    """POST com mesmo serviço+causa raiz deve retornar 201 com duplicate_warning."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    app = _make_app(store)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/knowledge/patterns", json=_VALID_PAYLOAD)
        resp = await client.post("/api/v1/knowledge/patterns", json=_VALID_PAYLOAD)

    assert resp.status_code == 201
    data = resp.json()
    assert data["duplicate_warning"] is not None
    assert "payment-svc" in data["duplicate_warning"]


@pytest.mark.asyncio
async def test_create_pattern_invalid_confidence_returns_422(tmp_path):
    """POST com confidence > 1.0 deve retornar 422."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    app = _make_app(store)

    invalid = {**_VALID_PAYLOAD, "confidence": 1.5}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/knowledge/patterns", json=invalid)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_pattern_empty_symptoms_returns_422(tmp_path):
    """POST com lista de symptoms vazia deve retornar 422."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    app = _make_app(store)

    invalid = {**_VALID_PAYLOAD, "symptoms": []}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/v1/knowledge/patterns", json=invalid)

    assert resp.status_code == 422


# ── GET /api/v1/knowledge/patterns ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_patterns_empty_store(tmp_path):
    """GET com store vazio deve retornar total=0 e lista vazia."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    app = _make_app(store)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/knowledge/patterns")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 0
    assert data["patterns"] == []


@pytest.mark.asyncio
async def test_list_patterns_includes_added_patterns(tmp_path):
    """GET deve retornar todos os padrões adicionados."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    app = _make_app(store)

    payload2 = {**_VALID_PAYLOAD, "service": "api-svc", "root_cause": "Memory leak"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/api/v1/knowledge/patterns", json=_VALID_PAYLOAD)
        await client.post("/api/v1/knowledge/patterns", json=payload2)
        resp = await client.get("/api/v1/knowledge/patterns")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 2
    assert len(data["patterns"]) == 2
    services = {p["service"] for p in data["patterns"]}
    assert services == {"payment-svc", "api-svc"}
