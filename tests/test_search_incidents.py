"""
Testes para a ferramenta MCP search_incidents (Story 5.4).

Verifica busca RAG, filtragem, sanitização de PII e tratamento de erros.
"""

from __future__ import annotations

import json

import pytest


def _make_mock_result(
    incident_id="inc-001",
    summary="Payment service timeout",
    diagnosis="Connection pool exhausted",
    resolution="Rollback config",
    confidence=0.9,
    score=0.85,
    service="payment-svc",
    tags=None,
):
    """Cria mock de SearchResult."""

    class MockResult:
        pass

    r = MockResult()
    r.incident_id = incident_id
    r.summary = summary
    r.diagnosis = diagnosis
    r.resolution = resolution
    r.confidence = confidence
    r.score = score
    r.service = service
    r.tags = tags or ["payment", "timeout"]
    return r


@pytest.mark.unit
class TestHandleSearchIncidents:
    """Testes para handle_search_incidents()."""

    @pytest.mark.asyncio
    async def test_valid_query_returns_results(self):
        """Verifica que query válida retorna resultados com campos esperados."""
        from mcp_server.tools import handle_search_incidents

        mock_result = _make_mock_result()

        class MockRetriever:
            def search(self, query, top_k=5):
                return [mock_result]

        result_json = await handle_search_incidents(
            {"query": "payment timeout"},
            retriever=MockRetriever(),
        )
        data = json.loads(result_json)

        assert "results" in data
        assert data["total"] == 1
        assert data["query"] == "payment timeout"
        assert "latency_ms" in data

        r = data["results"][0]
        assert r["incident_id"] == "inc-001"
        assert r["summary"] == "Payment service timeout"
        assert r["diagnosis"] == "Connection pool exhausted"
        assert r["resolution"] == "Rollback config"
        assert r["confidence"] == 0.9
        assert r["similarity"] == 0.85

    @pytest.mark.asyncio
    async def test_top_k_limits_results(self):
        """Verifica que top_k=3 retorna no máximo 3 resultados."""
        from mcp_server.tools import handle_search_incidents

        class MockRetriever:
            def search(self, query, top_k=5):
                return [_make_mock_result(incident_id=f"inc-{i}") for i in range(10)]

        result_json = await handle_search_incidents(
            {"query": "timeout", "top_k": 3},
            retriever=MockRetriever(),
        )
        data = json.loads(result_json)
        assert data["total"] <= 3

    @pytest.mark.asyncio
    async def test_service_filter_narrows_results(self):
        """Verifica que filtro de serviço remove resultados de outros serviços."""
        from mcp_server.tools import handle_search_incidents

        class MockRetriever:
            def search(self, query, top_k=5):
                return [
                    _make_mock_result(incident_id="inc-1", service="payment-svc"),
                    _make_mock_result(incident_id="inc-2", service="auth-svc"),
                    _make_mock_result(incident_id="inc-3", service="payment-svc"),
                ]

        result_json = await handle_search_incidents(
            {"query": "timeout", "service": "payment"},
            retriever=MockRetriever(),
        )
        data = json.loads(result_json)
        for r in data["results"]:
            assert "payment" in r["service"].lower()

    @pytest.mark.asyncio
    async def test_faiss_unavailable_returns_graceful_error(self):
        """Verifica que FAISS indisponível retorna erro gracioso (não crash)."""
        from mcp_server.tools import handle_search_incidents

        class BrokenRetriever:
            def search(self, query, top_k=5):
                raise RuntimeError("FAISS index not loaded")

        result_json = await handle_search_incidents(
            {"query": "timeout"},
            retriever=BrokenRetriever(),
        )
        data = json.loads(result_json)
        assert data["type"] == "faiss_unavailable"
        assert "title" in data

    @pytest.mark.asyncio
    async def test_pii_sanitized_in_results(self):
        """Verifica que PII é sanitizado antes de retornar resultados."""
        from mcp_server.tools import handle_search_incidents

        sanitized_calls = []

        class MockSanitizer:
            def sanitize_text(self, text):
                sanitized_calls.append(text)
                return "[SANITIZED]"

        class MockRetriever:
            def search(self, query, top_k=5):
                return [_make_mock_result(summary="User john@example.com had issue")]

        result_json = await handle_search_incidents(
            {"query": "pii test"},
            retriever=MockRetriever(),
            sanitizer=MockSanitizer(),
        )
        data = json.loads(result_json)
        # Sanitizer foi chamado
        assert len(sanitized_calls) > 0

    @pytest.mark.asyncio
    async def test_empty_query_returns_validation_error(self):
        """Verifica que query vazia retorna erro de validação."""
        from mcp_server.tools import handle_search_incidents

        class MockRetriever:
            def search(self, query, top_k=5):
                return []

        result_json = await handle_search_incidents(
            {"query": ""},
            retriever=MockRetriever(),
        )
        data = json.loads(result_json)
        assert data["type"] == "validation_error"

    @pytest.mark.asyncio
    async def test_latency_measured_and_in_response(self):
        """Verifica que latência é medida e incluída na resposta."""
        from mcp_server.tools import handle_search_incidents

        class MockRetriever:
            def search(self, query, top_k=5):
                return []

        result_json = await handle_search_incidents(
            {"query": "latency test"},
            retriever=MockRetriever(),
        )
        data = json.loads(result_json)
        assert "latency_ms" in data
        assert data["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_top_k_capped_at_20(self):
        """Verifica que top_k acima de 20 é limitado a 20."""
        from mcp_server.tools import handle_search_incidents

        received_top_k = []

        class MockRetriever:
            def search(self, query, top_k=5):
                received_top_k.append(top_k)
                return []

        await handle_search_incidents(
            {"query": "test", "top_k": 100},
            retriever=MockRetriever(),
        )
        # top_k máximo = 20 (dobrado para compensar filtragem = 40)
        assert received_top_k[0] <= 40
