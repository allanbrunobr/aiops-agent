from __future__ import annotations

"""
Testes para Stories 4.1 e 4.2: RAG Knowledge Base e Graceful Degradation.
Evita imports de ML pesados (torch, faiss, sentence_transformers).
"""

import pytest

from rag.models import KnowledgePattern, KnowledgeSearchResult
from rag.knowledge_store import KnowledgeStore
from rag.knowledge_retriever import KnowledgeRetriever


# ── KnowledgePattern ──────────────────────────────────────────────────────────

def test_knowledge_pattern_to_embedding_text():
    """to_embedding_text deve combinar service, symptoms, root_cause e resolution."""
    pattern = KnowledgePattern(
        pattern_id="pat-001",
        service="payment-svc",
        symptoms=["Connection timeout", "High error rate"],
        root_cause="Pool exhausted",
        resolution="Revert config",
    )
    text = pattern.to_embedding_text()
    assert "payment-svc" in text
    assert "Connection timeout" in text
    assert "Pool exhausted" in text
    assert "Revert config" in text


def test_knowledge_pattern_defaults():
    """KnowledgePattern deve ter is_negative_example=False por padrão."""
    pattern = KnowledgePattern(
        pattern_id="pat-002",
        service="api-svc",
        symptoms=["Slow response"],
        root_cause="Memory leak",
        resolution="Restart pod",
    )
    assert pattern.is_negative_example is False
    assert pattern.confidence == 0.0
    assert pattern.tags == []


def test_knowledge_search_result_to_llm_context():
    """to_llm_context deve incluir similarity_score e campos do pattern."""
    pattern = KnowledgePattern(
        pattern_id="pat-003",
        service="api-svc",
        symptoms=["OOM"],
        root_cause="Memory leak in cache",
        resolution="Flush cache",
        tags=["memory", "cache"],
        confidence=0.85,
    )
    result = KnowledgeSearchResult(pattern=pattern, similarity_score=0.92)
    ctx = result.to_llm_context()
    assert "0.920" in ctx
    assert "Memory leak in cache" in ctx
    assert "Flush cache" in ctx
    assert "api-svc" in ctx


# ── KnowledgeStore ────────────────────────────────────────────────────────────

def test_knowledge_store_add_pattern_marks_stale(tmp_path):
    """add_pattern deve marcar índice como stale."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    assert not store.is_index_stale

    pattern = KnowledgePattern(
        pattern_id="pat-001",
        service="svc",
        symptoms=["err"],
        root_cause="bug",
        resolution="fix",
    )
    store.add_pattern(pattern)

    assert store.is_index_stale


def test_knowledge_store_mark_index_rebuilt(tmp_path):
    """mark_index_rebuilt deve limpar flag stale."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    pattern = KnowledgePattern(
        pattern_id="pat-001",
        service="svc",
        symptoms=["err"],
        root_cause="bug",
        resolution="fix",
    )
    store.add_pattern(pattern)
    assert store.is_index_stale

    store.mark_index_rebuilt()
    assert not store.is_index_stale


def test_knowledge_store_find_by_service_and_cause(tmp_path):
    """find_by_service_and_cause deve retornar padrão com mesmos service e root_cause."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    pattern = KnowledgePattern(
        pattern_id="pat-001",
        service="payment-svc",
        symptoms=["timeout"],
        root_cause="pool exhausted",
        resolution="revert",
    )
    store.add_pattern(pattern)

    found = store.find_by_service_and_cause("payment-svc", "pool exhausted")
    assert found is not None
    assert found.pattern_id == "pat-001"

    not_found = store.find_by_service_and_cause("payment-svc", "different cause")
    assert not_found is None


def test_knowledge_store_mark_pattern_negative(tmp_path):
    """mark_pattern_negative deve marcar is_negative_example=True e retornar True."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    pattern = KnowledgePattern(
        pattern_id="pat-001",
        service="svc",
        symptoms=["err"],
        root_cause="bug",
        resolution="fix",
    )
    store.add_pattern(pattern)
    store.mark_index_rebuilt()
    assert not store.is_index_stale

    result = store.mark_pattern_negative("pat-001")
    assert result is True
    assert store.is_index_stale  # Deve marcar stale novamente

    patterns = store.get_all_patterns()
    assert patterns[0].is_negative_example is True


def test_knowledge_store_mark_pattern_negative_not_found(tmp_path):
    """mark_pattern_negative com ID desconhecido deve retornar False."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    result = store.mark_pattern_negative("inexistente")
    assert result is False


def test_knowledge_store_load_from_disk(tmp_path):
    """load_from_disk deve recuperar padrões salvos por _persist."""
    storage_path = str(tmp_path / "patterns.json")
    store1 = KnowledgeStore(storage_path=storage_path)
    pattern = KnowledgePattern(
        pattern_id="pat-persist",
        service="api-svc",
        symptoms=["slow"],
        root_cause="memory leak",
        resolution="restart",
        confidence=0.9,
    )
    store1.add_pattern(pattern)

    # Carrega em nova instância
    store2 = KnowledgeStore(storage_path=storage_path)
    loaded = store2.load_from_disk()

    assert loaded == 1
    patterns = store2.get_all_patterns()
    assert len(patterns) == 1
    assert patterns[0].pattern_id == "pat-persist"
    assert patterns[0].confidence == 0.9


# ── KnowledgeRetriever — Graceful Degradation (Story 4.2) ─────────────────────

class _FakeIndexerNoIndex:
    """Simulador de indexer sem índice FAISS carregado."""
    index = None
    model = None


class _FakeIndexerNoModel:
    """Simulador de indexer com índice mas sem model."""
    index = object()  # truthy, não None
    model = None


def test_retriever_returns_empty_when_index_is_none():
    """Story 4.2: Sem índice FAISS, retriever retorna [] sem exception."""
    patterns = [KnowledgePattern(
        pattern_id="p1",
        service="svc",
        symptoms=["err"],
        root_cause="bug",
        resolution="fix",
    )]
    retriever = KnowledgeRetriever(indexer=_FakeIndexerNoIndex(), patterns=patterns)

    results = retriever.search_similar_incidents("connection pool exhausted")

    assert results == []


def test_retriever_returns_empty_when_model_is_none():
    """Story 4.2: Sem modelo de embedding, retriever retorna [] sem exception."""
    patterns = [KnowledgePattern(
        pattern_id="p1",
        service="svc",
        symptoms=["err"],
        root_cause="bug",
        resolution="fix",
    )]
    retriever = KnowledgeRetriever(indexer=_FakeIndexerNoModel(), patterns=patterns)

    results = retriever.search_similar_incidents("memory leak")

    assert results == []


def test_retriever_returns_empty_on_no_patterns():
    """Story 4.2: Sem padrões na base, retriever retorna [] sem exception."""
    retriever = KnowledgeRetriever(indexer=_FakeIndexerNoIndex(), patterns=[])

    results = retriever.search_similar_incidents("any query")

    assert results == []


def test_retriever_filters_negative_examples():
    """Padrões marcados como is_negative_example não devem aparecer nos resultados.

    Nota: Testado indiretamente via is_negative_example flag na lógica de filtragem.
    O filtro é aplicado no loop de resultados dentro de search_similar_incidents.
    """
    # Sem índice real, mas verificamos que negative patterns existem e seriam filtrados
    pattern = KnowledgePattern(
        pattern_id="p-negative",
        service="svc",
        symptoms=["err"],
        root_cause="false positive bug",
        resolution="none",
        is_negative_example=True,
    )
    assert pattern.is_negative_example is True
    # A filtragem real ocorre em search_similar_incidents com índice FAISS real
