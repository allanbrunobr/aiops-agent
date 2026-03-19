"""
Testes de integracao profundos para o modulo RAG do AIOps Agent.

Cobre cenarios avancados de KnowledgeRetriever, KnowledgeStore,
persistencia com dados reais, formatacao LLM, e pipeline completo
EnrichmentNode / CotRetriever / DomainReranker.

Evita imports pesados (torch, faiss, sentence_transformers) no topo.
Usa mocks leves para simular FAISS e modelos de embedding.
"""
from __future__ import annotations

import json
import datetime
from unittest.mock import MagicMock

import numpy as np
import pytest

from rag.models import KnowledgePattern, KnowledgeSearchResult
from rag.knowledge_store import KnowledgeStore
from rag.knowledge_retriever import KnowledgeRetriever
from rag.enrichment_node import RAGEnrichmentNode
from rag.cot_retriever import CoTObservabilityRetriever, SearchContext
from rag.query_expansion import QueryExpander
from rag.reranker import DomainReranker, RankSignals


# ── Helpers e Fakes ──────────────────────────────────────────────────────────


def _make_pattern(
    pattern_id: str = "pat-001",
    service: str = "payment-svc",
    symptoms: list[str] | None = None,
    root_cause: str = "Pool exhausted",
    resolution: str = "Revert config",
    tags: list[str] | None = None,
    confidence: float = 0.85,
    is_negative: bool = False,
    created_at: str | None = None,
) -> KnowledgePattern:
    """Helper para criar KnowledgePattern com defaults razoaveis."""
    return KnowledgePattern(
        pattern_id=pattern_id,
        service=service,
        symptoms=symptoms or ["Connection timeout", "High error rate"],
        root_cause=root_cause,
        resolution=resolution,
        tags=tags or ["connection", "pool"],
        confidence=confidence,
        is_negative_example=is_negative,
        created_at=created_at or datetime.datetime.now(datetime.timezone.utc).isoformat(),
    )


class _MockModel:
    """Modelo de embedding falso que retorna vetores deterministicos."""

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> np.ndarray:
        """Retorna vetor unitario baseado no hash do texto."""
        dim = 8
        results = []
        for text in texts:
            h = hash(text) % 10000
            vec = np.array([h + i for i in range(dim)], dtype=np.float32)
            norm = np.linalg.norm(vec)
            if norm > 0:
                vec = vec / norm
            results.append(vec)
        return np.array(results, dtype=np.float32)


class _MockIndex:
    """Indice FAISS falso que retorna resultados pre-configurados."""

    def __init__(self, distances: np.ndarray, indices: np.ndarray):
        self._distances = distances
        self._indices = indices

    def search(self, query: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        """Retorna distancias e indices configurados, limitando a k."""
        return self._distances[:, :k], self._indices[:, :k]


class _FakeIndexerComplete:
    """Indexer com model e index funcionais (mocks)."""

    def __init__(self, distances: np.ndarray, indices: np.ndarray):
        self.model = _MockModel()
        self.index = _MockIndex(distances, indices)


class _FakeIndexerNoIndex:
    """Indexer sem indice FAISS."""
    index = None
    model = _MockModel()


class _FakeIndexerNoModel:
    """Indexer com indice mas sem modelo."""
    index = object()
    model = None


class _FakeIndexerNullBoth:
    """Indexer sem nenhum atributo index/model."""
    pass


class _FakeChunk:
    """Chunk fake para SearchResult de ObservabilityRetriever."""

    def __init__(self, text: str, source: str = "log", metadata: dict | None = None):
        self.text = text
        self.source = source
        self.metadata = metadata or {}


class _FakeSearchResult:
    """SearchResult fake para testes de reranker e pipeline."""

    def __init__(
        self,
        text: str,
        score: float = 0.9,
        source: str = "log",
        metadata: dict | None = None,
    ):
        self.chunk = _FakeChunk(text, source, metadata or {})
        self.score = score


class _FakeObsIndexer:
    """ObservabilityIndexer fake — FAISS indisponivel."""
    index = None
    model = None


# ==============================================================================
# 1. KnowledgeRetriever com mock de indexer
# ==============================================================================


@pytest.mark.unit
class TestKnowledgeRetrieverWithMocks:
    """Testes profundos do KnowledgeRetriever usando mocks controlados."""

    def test_index_none_returns_empty_gracefully(self):
        """Indexer com index=None retorna [] sem lancar excecao."""
        patterns = [_make_pattern()]
        retriever = KnowledgeRetriever(indexer=_FakeIndexerNoIndex(), patterns=patterns)
        results = retriever.search_similar_incidents("connection pool exhausted")
        assert results == []

    def test_model_none_returns_empty_gracefully(self):
        """Indexer com model=None retorna [] sem lancar excecao."""
        patterns = [_make_pattern()]
        retriever = KnowledgeRetriever(indexer=_FakeIndexerNoModel(), patterns=patterns)
        results = retriever.search_similar_incidents("memory leak detected")
        assert results == []

    def test_no_attributes_returns_empty_gracefully(self):
        """Indexer sem atributos index/model retorna [] sem excecao."""
        patterns = [_make_pattern()]
        retriever = KnowledgeRetriever(indexer=_FakeIndexerNullBoth(), patterns=patterns)
        results = retriever.search_similar_incidents("any query")
        assert results == []

    def test_search_returns_results_ordered_by_similarity(self):
        """Busca retorna resultados na ordem das distancias do FAISS."""
        patterns = [
            _make_pattern(pattern_id="p1", service="svc-a", root_cause="cause-a"),
            _make_pattern(pattern_id="p2", service="svc-b", root_cause="cause-b"),
            _make_pattern(pattern_id="p3", service="svc-c", root_cause="cause-c"),
        ]
        # FAISS retorna indices [2, 0, 1] com distancias decrescentes
        distances = np.array([[0.95, 0.90, 0.80]], dtype=np.float32)
        indices = np.array([[2, 0, 1]], dtype=np.int64)
        indexer = _FakeIndexerComplete(distances, indices)

        retriever = KnowledgeRetriever(indexer=indexer, patterns=patterns)
        results = retriever.search_similar_incidents("test query", top_k=3)

        assert len(results) == 3
        assert results[0].pattern.pattern_id == "p3"
        assert results[0].similarity_score == pytest.approx(0.95)
        assert results[1].pattern.pattern_id == "p1"
        assert results[1].similarity_score == pytest.approx(0.90)
        assert results[2].pattern.pattern_id == "p2"
        assert results[2].similarity_score == pytest.approx(0.80)

    def test_negative_example_filtered_from_results(self):
        """Padrao com is_negative_example=True e excluido dos resultados."""
        patterns = [
            _make_pattern(pattern_id="p-good", is_negative=False),
            _make_pattern(pattern_id="p-bad", is_negative=True),
            _make_pattern(pattern_id="p-ok", is_negative=False),
        ]
        distances = np.array([[0.95, 0.90, 0.85]], dtype=np.float32)
        indices = np.array([[0, 1, 2]], dtype=np.int64)
        indexer = _FakeIndexerComplete(distances, indices)

        retriever = KnowledgeRetriever(indexer=indexer, patterns=patterns)
        results = retriever.search_similar_incidents("query", top_k=5)

        pattern_ids = [r.pattern.pattern_id for r in results]
        assert "p-bad" not in pattern_ids
        assert "p-good" in pattern_ids
        assert "p-ok" in pattern_ids
        assert len(results) == 2

    def test_top_k_limits_results(self):
        """top_k=2 com 5 padroes disponiveis retorna exatamente 2."""
        patterns = [
            _make_pattern(pattern_id=f"p{i}", service=f"svc-{i}", root_cause=f"cause-{i}")
            for i in range(5)
        ]
        distances = np.array([[0.99, 0.95, 0.90, 0.85, 0.80]], dtype=np.float32)
        indices = np.array([[0, 1, 2, 3, 4]], dtype=np.int64)
        indexer = _FakeIndexerComplete(distances, indices)

        retriever = KnowledgeRetriever(indexer=indexer, patterns=patterns)
        results = retriever.search_similar_incidents("query", top_k=2)

        assert len(results) == 2

    def test_exception_in_index_search_returns_empty(self):
        """Excecao no index.search() retorna [] sem propagar."""
        patterns = [_make_pattern()]
        mock_index = MagicMock()
        mock_index.search.side_effect = RuntimeError("FAISS corruption")

        indexer = MagicMock()
        indexer.index = mock_index
        indexer.model = _MockModel()

        retriever = KnowledgeRetriever(indexer=indexer, patterns=patterns)
        results = retriever.search_similar_incidents("broken query")
        assert results == []

    def test_exception_in_model_encode_returns_empty(self):
        """Excecao no model.encode() retorna [] sem propagar."""
        patterns = [_make_pattern()]
        mock_model = MagicMock()
        mock_model.encode.side_effect = RuntimeError("Model load failed")

        indexer = MagicMock()
        indexer.index = MagicMock()
        indexer.model = mock_model

        retriever = KnowledgeRetriever(indexer=indexer, patterns=patterns)
        results = retriever.search_similar_incidents("query")
        assert results == []

    def test_invalid_faiss_index_negative_one_skipped(self):
        """Indice -1 retornado pelo FAISS (padding) e ignorado."""
        patterns = [_make_pattern(pattern_id="p0")]
        distances = np.array([[0.95, 0.0]], dtype=np.float32)
        indices = np.array([[0, -1]], dtype=np.int64)
        indexer = _FakeIndexerComplete(distances, indices)

        retriever = KnowledgeRetriever(indexer=indexer, patterns=patterns)
        results = retriever.search_similar_incidents("query", top_k=5)

        assert len(results) == 1
        assert results[0].pattern.pattern_id == "p0"

    def test_out_of_bounds_index_skipped(self):
        """Indice >= len(patterns) retornado pelo FAISS e ignorado."""
        patterns = [_make_pattern(pattern_id="p0")]
        distances = np.array([[0.95, 0.90]], dtype=np.float32)
        indices = np.array([[0, 99]], dtype=np.int64)
        indexer = _FakeIndexerComplete(distances, indices)

        retriever = KnowledgeRetriever(indexer=indexer, patterns=patterns)
        results = retriever.search_similar_incidents("query", top_k=5)

        assert len(results) == 1


# ==============================================================================
# 2. KnowledgeStore com escalas
# ==============================================================================


@pytest.mark.unit
class TestKnowledgeStoreAtScale:
    """Testes de KnowledgeStore com volumes maiores e cenarios de borda."""

    def test_add_100_patterns_returns_all(self, tmp_path):
        """Adicionar 100 padroes e verificar que get_all_patterns() retorna 100."""
        store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
        for i in range(100):
            store.add_pattern(_make_pattern(
                pattern_id=f"pat-{i:03d}",
                service=f"svc-{i % 10}",
                root_cause=f"cause-{i}",
            ))

        all_patterns = store.get_all_patterns()
        assert len(all_patterns) == 100

    def test_mark_pattern_negative_nonexistent_returns_false(self, tmp_path):
        """mark_pattern_negative() em pattern inexistente retorna False."""
        store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
        store.add_pattern(_make_pattern(pattern_id="existing"))
        result = store.mark_pattern_negative("nonexistent-id")
        assert result is False

    def test_is_index_stale_lifecycle(self, tmp_path):
        """is_index_stale fica True apos add, False apos mark_index_rebuilt."""
        store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
        assert store.is_index_stale is False

        store.add_pattern(_make_pattern(pattern_id="p1"))
        assert store.is_index_stale is True

        store.mark_index_rebuilt()
        assert store.is_index_stale is False

        store.add_pattern(_make_pattern(pattern_id="p2"))
        assert store.is_index_stale is True

    def test_mark_negative_also_marks_stale(self, tmp_path):
        """mark_pattern_negative() marca indice como stale."""
        store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
        store.add_pattern(_make_pattern(pattern_id="p1"))
        store.mark_index_rebuilt()
        assert store.is_index_stale is False

        store.mark_pattern_negative("p1")
        assert store.is_index_stale is True

    def test_find_by_service_and_cause_multiple_services(self, tmp_path):
        """find_by_service_and_cause() com multiplos servicos retorna o correto."""
        store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))

        store.add_pattern(_make_pattern(
            pattern_id="p-payment", service="payment-svc", root_cause="pool exhausted",
        ))
        store.add_pattern(_make_pattern(
            pattern_id="p-auth", service="auth-svc", root_cause="token expired",
        ))
        store.add_pattern(_make_pattern(
            pattern_id="p-gateway", service="gateway-svc", root_cause="rate limit",
        ))

        found = store.find_by_service_and_cause("auth-svc", "token expired")
        assert found is not None
        assert found.pattern_id == "p-auth"

        not_found = store.find_by_service_and_cause("auth-svc", "pool exhausted")
        assert not_found is None

    def test_find_by_service_and_cause_empty_store(self, tmp_path):
        """find_by_service_and_cause() em store vazio retorna None."""
        store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
        assert store.find_by_service_and_cause("any-svc", "any-cause") is None

    def test_get_all_patterns_returns_copy(self, tmp_path):
        """get_all_patterns() retorna copia — modificacoes nao afetam o store."""
        store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
        store.add_pattern(_make_pattern(pattern_id="p1"))

        patterns_list = store.get_all_patterns()
        patterns_list.clear()

        assert len(store.get_all_patterns()) == 1


# ==============================================================================
# 3. Persistencia com dados reais
# ==============================================================================


@pytest.mark.integration
class TestPersistenceWithRealData:
    """Testes de persistencia JSON usando tmp_path para I/O real."""

    def test_empty_storage_path_no_persist_no_error(self):
        """Store com storage_path='' nao persiste e nao lanca erro."""
        store = KnowledgeStore(storage_path="")
        store.add_pattern(_make_pattern(pattern_id="p1"))
        all_p = store.get_all_patterns()
        assert len(all_p) == 1

    def test_roundtrip_50_patterns_all_fields(self, tmp_path):
        """Salvar e carregar 50 padroes preservando todos os campos."""
        storage_path = str(tmp_path / "subdir" / "patterns.json")
        store1 = KnowledgeStore(storage_path=storage_path)

        original_patterns = []
        for i in range(50):
            p = _make_pattern(
                pattern_id=f"pat-{i:03d}",
                service=f"svc-{i % 5}",
                root_cause=f"cause-{i}",
                resolution=f"fix-{i}",
                tags=[f"tag-{i}", f"category-{i % 3}"],
                confidence=round(0.5 + (i % 50) * 0.01, 2),
                is_negative=(i % 7 == 0),
                created_at=f"2026-03-{(i % 28) + 1:02d}T10:00:00+00:00",
            )
            original_patterns.append(p)
            store1.add_pattern(p)

        store2 = KnowledgeStore(storage_path=storage_path)
        loaded_count = store2.load_from_disk()

        assert loaded_count == 50
        loaded = store2.get_all_patterns()
        assert len(loaded) == 50

        for orig, loaded_p in zip(original_patterns, loaded):
            assert loaded_p.pattern_id == orig.pattern_id
            assert loaded_p.service == orig.service
            assert loaded_p.symptoms == orig.symptoms
            assert loaded_p.root_cause == orig.root_cause
            assert loaded_p.resolution == orig.resolution
            assert loaded_p.tags == orig.tags
            assert loaded_p.confidence == orig.confidence
            assert loaded_p.is_negative_example == orig.is_negative_example
            assert loaded_p.created_at == orig.created_at

    def test_corrupted_json_returns_zero(self, tmp_path):
        """Arquivo JSON corrompido — load_from_disk() retorna 0 sem crash."""
        storage_path = str(tmp_path / "patterns.json")
        with open(storage_path, "w", encoding="utf-8") as f:
            f.write("{{{invalid json content, not valid at all")

        store = KnowledgeStore(storage_path=storage_path)
        loaded = store.load_from_disk()
        assert loaded == 0
        assert store.get_all_patterns() == []

    def test_created_at_preserved_roundtrip(self, tmp_path):
        """created_at e preservado no round-trip JSON sem alteracao."""
        storage_path = str(tmp_path / "patterns.json")
        fixed_ts = "2026-01-15T14:30:00+00:00"

        store1 = KnowledgeStore(storage_path=storage_path)
        store1.add_pattern(_make_pattern(pattern_id="p-ts", created_at=fixed_ts))

        store2 = KnowledgeStore(storage_path=storage_path)
        store2.load_from_disk()
        loaded = store2.get_all_patterns()

        assert len(loaded) == 1
        assert loaded[0].created_at == fixed_ts

    def test_nonexistent_file_load_returns_zero(self, tmp_path):
        """load_from_disk() com arquivo inexistente retorna 0."""
        store = KnowledgeStore(storage_path=str(tmp_path / "does_not_exist.json"))
        assert store.load_from_disk() == 0

    def test_empty_json_array_loads_zero_patterns(self, tmp_path):
        """Arquivo JSON com array vazio carrega 0 padroes sem erro."""
        storage_path = str(tmp_path / "patterns.json")
        with open(storage_path, "w", encoding="utf-8") as f:
            json.dump([], f)

        store = KnowledgeStore(storage_path=storage_path)
        loaded = store.load_from_disk()
        assert loaded == 0

    def test_partial_corrupt_json_fields_fails_gracefully(self, tmp_path):
        """JSON com campos faltantes — load_from_disk() retorna 0."""
        storage_path = str(tmp_path / "patterns.json")
        with open(storage_path, "w", encoding="utf-8") as f:
            json.dump([{"pattern_id": "p1"}], f)

        store = KnowledgeStore(storage_path=storage_path)
        loaded = store.load_from_disk()
        assert loaded == 0


# ==============================================================================
# 4. KnowledgeSearchResult.to_llm_context()
# ==============================================================================


@pytest.mark.unit
class TestSearchResultFormatting:
    """Testes de formatacao do to_llm_context() para consumo pelo LLM."""

    def test_similarity_score_three_decimal_places(self):
        """similarity_score e formatado com exatamente 3 casas decimais."""
        pattern = _make_pattern(confidence=0.85)
        result = KnowledgeSearchResult(pattern=pattern, similarity_score=0.92345)
        ctx = result.to_llm_context()
        assert "similarity=0.923" in ctx

    def test_confidence_two_decimal_places(self):
        """confidence e formatado com exatamente 2 casas decimais."""
        pattern = _make_pattern(confidence=0.8)
        result = KnowledgeSearchResult(pattern=pattern, similarity_score=0.9)
        ctx = result.to_llm_context()
        assert "confidence=0.80" in ctx

    def test_includes_service_root_cause_resolution(self):
        """Contexto inclui service, root_cause e resolution do padrao."""
        pattern = _make_pattern(
            service="order-svc",
            root_cause="Database deadlock",
            resolution="Increase lock timeout",
        )
        result = KnowledgeSearchResult(pattern=pattern, similarity_score=0.88)
        ctx = result.to_llm_context()

        assert "order-svc" in ctx
        assert "Database deadlock" in ctx
        assert "Increase lock timeout" in ctx

    def test_includes_tags(self):
        """Contexto inclui tags do padrao."""
        pattern = _make_pattern(tags=["database", "deadlock", "timeout"])
        result = KnowledgeSearchResult(pattern=pattern, similarity_score=0.75)
        ctx = result.to_llm_context()

        assert "database" in ctx
        assert "deadlock" in ctx
        assert "timeout" in ctx

    def test_empty_tags_no_crash(self):
        """Padrao sem tags nao quebra a formatacao."""
        pattern = _make_pattern(tags=[])
        result = KnowledgeSearchResult(pattern=pattern, similarity_score=0.5)
        ctx = result.to_llm_context()
        assert "Tags:" in ctx
        assert "[Similar Incident]" in ctx

    def test_negative_example_formatting_still_works(self):
        """Negative example (se passar o filtro) nao quebra a formatacao."""
        pattern = _make_pattern(is_negative=True)
        result = KnowledgeSearchResult(pattern=pattern, similarity_score=0.6)
        ctx = result.to_llm_context()
        assert isinstance(ctx, str)
        assert "similarity=0.600" in ctx

    def test_zero_similarity_formatted(self):
        """Similarity score 0.0 e formatado corretamente."""
        pattern = _make_pattern()
        result = KnowledgeSearchResult(pattern=pattern, similarity_score=0.0)
        ctx = result.to_llm_context()
        assert "similarity=0.000" in ctx

    def test_perfect_similarity_formatted(self):
        """Similarity score 1.0 e formatado corretamente."""
        pattern = _make_pattern()
        result = KnowledgeSearchResult(pattern=pattern, similarity_score=1.0)
        ctx = result.to_llm_context()
        assert "similarity=1.000" in ctx


# ==============================================================================
# 5. EnrichmentNode — pipeline completo com fallback
# ==============================================================================


class _MockKnowledgeRetriever:
    """KnowledgeRetriever controlavel para testes de EnrichmentNode."""

    def __init__(
        self,
        results: list[KnowledgeSearchResult] | None = None,
        raises: Exception | None = None,
    ):
        self._results = results or []
        self._raises = raises
        self.last_query: str | None = None

    def search_similar_incidents(
        self, query: str, top_k: int = 5,
    ) -> list[KnowledgeSearchResult]:
        self.last_query = query
        if self._raises:
            raise self._raises
        return self._results


@pytest.mark.unit
class TestEnrichmentNodePipeline:
    """Testes do RAGEnrichmentNode com cenarios de integracao."""

    def test_enrichment_with_results(self):
        """EnrichmentNode injeta rag_enrichment no contexto quando ha resultados."""
        results = [
            KnowledgeSearchResult(
                pattern=_make_pattern(service="pay-svc", root_cause="pool exhaust"),
                similarity_score=0.92,
            ),
        ]
        retriever = _MockKnowledgeRetriever(results=results)
        node = RAGEnrichmentNode(knowledge_retriever=retriever, top_k=3)

        context = {
            "symptoms": ["Connection timeout"],
            "affected_services": ["payment-svc"],
            "error_summary": "High error rate on payment service",
        }
        result = node.execute(context)

        assert result["rag_available"] is True
        assert "pool exhaust" in result["rag_enrichment"]
        assert "pay-svc" in result["rag_enrichment"]

    def test_enrichment_no_results(self):
        """EnrichmentNode retorna rag_enrichment vazio quando sem resultados."""
        retriever = _MockKnowledgeRetriever(results=[])
        node = RAGEnrichmentNode(knowledge_retriever=retriever)

        context = {
            "symptoms": ["Unknown symptom"],
            "affected_services": ["unknown-svc"],
            "error_summary": "",
        }
        result = node.execute(context)

        assert result["rag_available"] is True
        assert result["rag_enrichment"] == ""

    def test_enrichment_exception_fallback(self):
        """EnrichmentNode trata excecao do retriever graciosamente."""
        retriever = _MockKnowledgeRetriever(raises=RuntimeError("FAISS corrupted"))
        node = RAGEnrichmentNode(knowledge_retriever=retriever)

        context = {
            "symptoms": ["error"],
            "affected_services": ["svc"],
            "error_summary": "test",
        }
        result = node.execute(context)

        assert result["rag_available"] is False
        assert "search_error" in result["rag_unavailable_reason"]
        assert result["rag_enrichment"] == ""

    def test_enrichment_builds_query_from_context(self):
        """EnrichmentNode constroi query a partir de symptoms, services e error_summary."""
        retriever = _MockKnowledgeRetriever(results=[])
        node = RAGEnrichmentNode(knowledge_retriever=retriever)

        context = {
            "symptoms": ["High latency", "Connection refused"],
            "affected_services": ["api-svc", "db-svc"],
            "error_summary": "Database connection pool drained",
        }
        node.execute(context)

        assert retriever.last_query is not None
        assert "api-svc" in retriever.last_query
        assert "db-svc" in retriever.last_query
        assert "High latency" in retriever.last_query
        assert "Database connection pool drained" in retriever.last_query

    def test_enrichment_multiple_results_joined(self):
        """Multiplos resultados sao concatenados no contexto."""
        results = [
            KnowledgeSearchResult(
                pattern=_make_pattern(pattern_id="p1", root_cause="cause-1"),
                similarity_score=0.95,
            ),
            KnowledgeSearchResult(
                pattern=_make_pattern(pattern_id="p2", root_cause="cause-2"),
                similarity_score=0.85,
            ),
        ]
        retriever = _MockKnowledgeRetriever(results=results)
        node = RAGEnrichmentNode(knowledge_retriever=retriever, top_k=5)

        context = {"symptoms": ["err"], "affected_services": ["svc"], "error_summary": ""}
        result = node.execute(context)

        assert "cause-1" in result["rag_enrichment"]
        assert "cause-2" in result["rag_enrichment"]

    def test_enrichment_missing_context_keys_no_crash(self):
        """Contexto sem chaves esperadas nao causa excecao."""
        retriever = _MockKnowledgeRetriever(results=[])
        node = RAGEnrichmentNode(knowledge_retriever=retriever)

        context = {}
        result = node.execute(context)

        assert result["rag_available"] is True
        assert result["rag_enrichment"] == ""


# ==============================================================================
# 6. DomainReranker — fusao de sinais e ordenacao
# ==============================================================================


@pytest.mark.unit
class TestDomainRerankerIntegration:
    """Testes do DomainReranker com cenarios de fusao de scores."""

    def test_rerank_orders_by_fused_score(self):
        """Reranker reordena resultados com base na fusao de sinais."""
        reranker = DomainReranker(
            service_criticality_map={"payment-svc": 1.0, "logging-svc": 0.1},
        )
        r1 = _FakeSearchResult(
            "payment error", score=0.5,
            metadata={"service": "payment-svc", "level": "ERROR", "deviation": 8.0},
        )
        r2 = _FakeSearchResult(
            "log rotation", score=0.9,
            metadata={"service": "logging-svc", "level": "INFO", "deviation": 0.5},
        )

        results = reranker.rerank([r1, r2], query="payment failure", top_k=2)

        assert len(results) == 2
        # Payment-svc (criticidade alta + ERROR) deve vir primeiro
        assert results[0].chunk.text == "payment error"

    def test_rerank_empty_list(self):
        """Reranker com lista vazia retorna lista vazia."""
        reranker = DomainReranker()
        results = reranker.rerank([], query="test", top_k=5)
        assert results == []

    def test_rerank_respects_top_k(self):
        """Reranker retorna no maximo top_k resultados."""
        reranker = DomainReranker()
        results_in = [
            _FakeSearchResult(f"result-{i}", score=0.5 + i * 0.05)
            for i in range(10)
        ]
        results = reranker.rerank(results_in, query="test", top_k=3)
        assert len(results) == 3

    def test_rerank_without_metadata(self):
        """Resultados sem metadata nao causam excecao."""
        reranker = DomainReranker()
        r1 = _FakeSearchResult("no metadata", score=0.8, metadata={})
        results = reranker.rerank([r1], query="test", top_k=1)
        assert len(results) == 1


# ==============================================================================
# 7. CoTRetriever + QueryExpander — pipeline integrado
# ==============================================================================


@pytest.mark.unit
class TestCoTRetrieverQueryExpanderPipeline:
    """Testes do pipeline CoTRetriever -> QueryExpander integrado."""

    def test_multi_iteration_query_expansion(self):
        """Multiplas iteracoes de busca expandem progressivamente a query."""
        retriever = CoTObservabilityRetriever(_FakeObsIndexer())

        retriever.search_with_cot("payment-svc timeout")
        assert retriever.search_history[0].query_expansion is None

        retriever.search_with_cot("auth-service error")
        ctx2 = retriever.search_history[1]
        assert ctx2.query_expansion is not None
        assert "excluding already investigated" in ctx2.query_expansion
        assert "payment-svc" in ctx2.query_expansion

    def test_entity_extraction_metric_keywords(self):
        """Extrai entidades de metricas da query."""
        retriever = CoTObservabilityRetriever(_FakeObsIndexer())
        retriever.search_with_cot("latency spike on payment-svc with cpu usage")

        ctx = retriever.search_history[0]
        entities = ctx.entities
        assert "payment-svc" in entities
        assert "metric:latency" in entities
        assert "metric:cpu" in entities

    def test_reset_context_clears_expansion_state(self):
        """reset_context() limpa historico; proxima busca nao tem expansao."""
        retriever = CoTObservabilityRetriever(_FakeObsIndexer())
        retriever.search_with_cot("payment-svc timeout")
        retriever.search_with_cot("auth-service error")

        retriever.reset_context()

        retriever.search_with_cot("new investigation")
        assert retriever.search_history[0].query_expansion is None
        assert retriever.current_iteration == 1

    def test_query_expander_deduplicates_entities(self):
        """QueryExpander nao repete entidades ja vistas em iteracoes anteriores."""
        expander = QueryExpander()
        history = [
            SearchContext(1, "q1", [], ["payment-svc", "metric:latency"]),
            SearchContext(2, "q2", [], ["payment-svc", "metric:cpu"]),
        ]
        expanded = expander.expand_query("new query", history)

        exclusion_part = expanded.split("excluding already investigated: ")[1].split(")")[0]
        entities_list = [e.strip() for e in exclusion_part.split(",")]
        assert len(entities_list) == len(set(entities_list))

    def test_search_summary_format(self):
        """get_search_summary() retorna formato correto com todas as iteracoes."""
        retriever = CoTObservabilityRetriever(_FakeObsIndexer())
        retriever.search_with_cot("payment-svc timeout")
        retriever.search_with_cot("memory leak detected")

        summary = retriever.get_search_summary()

        assert "=== Search History ===" in summary
        assert "Iteration 1" in summary
        assert "Iteration 2" in summary
        assert "payment-svc timeout" in summary
        assert "memory leak detected" in summary

    def test_follow_up_connection_pool_pattern(self):
        """Follow-up e gerado para padrao de connection pool."""
        expander = QueryExpander()
        ctx = SearchContext(
            iteration=1,
            query="db error",
            results=[_FakeSearchResult("connection pool exhausted on database")],
            entities=["metric:connections"],
        )
        expanded = expander.expand_query("new query", [ctx])
        assert "follow-up: connection pool configuration and limits" in expanded


# ==============================================================================
# 8. RankSignals — testes de computacao de sinais
# ==============================================================================


@pytest.mark.unit
class TestRankSignalsComputation:
    """Testes da extracao e fusao de sinais do DomainReranker."""

    def test_fuse_scores_all_zeros(self):
        """Todos os sinais zerados produzem score final 0.0."""
        reranker = DomainReranker()
        signals = RankSignals()
        score = reranker._fuse_scores(signals)
        assert score == pytest.approx(0.0)

    def test_fuse_scores_all_ones(self):
        """Todos os sinais 1.0 produzem score final 1.0."""
        reranker = DomainReranker()
        signals = RankSignals(
            deviation_sigma=1.0,
            service_criticality=1.0,
            log_level_score=1.0,
            vector_score=1.0,
            temporal_recency=1.0,
            chunk_weight=1.0,
        )
        score = reranker._fuse_scores(signals)
        assert score == pytest.approx(1.0)

    def test_compute_signals_deviation_capped_at_one(self):
        """deviation_sigma e capped em 1.0 (raw_sigma / 10)."""
        reranker = DomainReranker()
        result = _FakeSearchResult("test", score=0.5, metadata={"deviation": 25.0})
        signals = reranker._compute_signals(result)
        assert signals.deviation_sigma == 1.0

    def test_compute_signals_service_default_criticality(self):
        """Servico desconhecido recebe criticidade default 0.5."""
        reranker = DomainReranker(service_criticality_map={"known-svc": 0.9})
        result = _FakeSearchResult("test", score=0.5, metadata={"service": "unknown-svc"})
        signals = reranker._compute_signals(result)
        assert signals.service_criticality == 0.5

    def test_compute_signals_log_level_mapping(self):
        """Niveis de log sao mapeados corretamente para scores."""
        reranker = DomainReranker()

        for level, expected in [("ERROR", 1.0), ("WARNING", 0.6), ("INFO", 0.3), ("DEBUG", 0.1)]:
            result = _FakeSearchResult("test", score=0.5, metadata={"level": level})
            signals = reranker._compute_signals(result)
            assert signals.log_level_score == expected, f"Falhou para nivel {level}"

    def test_temporal_recency_no_timestamps(self):
        """Sem timestamps, todos recebem recencia 0.5 (neutro)."""
        reranker = DomainReranker()
        results = [
            _FakeSearchResult("a", metadata={}),
            _FakeSearchResult("b", metadata={}),
        ]
        recency = reranker._compute_temporal_recency(results)
        assert recency == [0.5, 0.5]

    def test_temporal_recency_with_timestamps(self):
        """Resultado mais recente recebe 1.0, mais antigo 0.0."""
        reranker = DomainReranker()
        results = [
            _FakeSearchResult("old", metadata={"timestamp": 1000.0}),
            _FakeSearchResult("new", metadata={"timestamp": 2000.0}),
        ]
        recency = reranker._compute_temporal_recency(results)
        assert recency[0] == pytest.approx(0.0)
        assert recency[1] == pytest.approx(1.0)
