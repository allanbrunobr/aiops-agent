"""F6-2: reranker e CoT deixam de ser código morto — wired via config.rag.

Antes:
- ``DomainReranker`` nunca era instanciado em produção (build_rag e a CLI V0
  criavam o retriever sem reranker; ``ObservabilityRetrieverWithHistory`` nem
  aceitava o parâmetro).
- ``CoTObservabilityRetriever`` só existia em testes; ao ser ativado chamaria
  ``self.reranker.rerank(query, raw)`` com argumentos invertidos (AttributeError)
  e DEPOIS de ``search()`` já ter reranqueado (duplo rerank).
- O mapa de criticidade de ``config.yaml`` (raiz) não era lido por ninguém,
  apesar de o README dizer que alimentava o blast radius.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from app.builders import build_rag, build_reranker
from config.loader import AppConfig, RagConfig, load_config
from rag.cot_retriever import CoTObservabilityRetriever
from rag.reranker import DomainReranker
from rag.retriever import ObservabilityRetriever
from rag.retriever_history import ObservabilityRetrieverWithHistory


def _cfg(**rag) -> AppConfig:
    return AppConfig(rag=RagConfig(**rag))


# ── config ───────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_defaults_yaml_has_rag_block_and_it_matches_model():
    cfg = load_config()
    assert cfg.rag.reranker_enabled is True
    assert cfg.rag.cot_enabled is True
    assert cfg.rag.cross_encoder_enabled is False
    assert cfg.rag.service_criticality_map["payment-svc"] == 1.0
    assert cfg.rag.service_criticality_map["debug-svc"] == 0.1


@pytest.mark.unit
def test_rag_config_overridable_via_env(monkeypatch):
    monkeypatch.setenv("AIOPS_RAG__RERANKER_ENABLED", "false")
    monkeypatch.setenv("AIOPS_RAG__COT_ENABLED", "false")
    cfg = load_config()
    assert cfg.rag.reranker_enabled is False
    assert cfg.rag.cot_enabled is False


@pytest.mark.unit
def test_criticality_must_be_in_unit_interval():
    with pytest.raises(ValidationError):
        RagConfig(service_criticality_map={"x": 1.5})


# ── builders ─────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_build_rag_default_is_cot_with_reranker():
    indexer, retriever = build_rag()
    assert isinstance(retriever, CoTObservabilityRetriever)
    assert isinstance(retriever, ObservabilityRetriever)  # compat com callers
    assert isinstance(retriever.reranker, DomainReranker)
    assert retriever.indexer is indexer
    assert retriever.reranker.use_cross_encoder is False  # Tier 2 opt-in


@pytest.mark.unit
def test_build_rag_honours_config_switches():
    _, plain = build_rag(_cfg(reranker_enabled=False, cot_enabled=False))
    assert type(plain) is ObservabilityRetriever
    assert plain.reranker is None

    _, cot_only = build_rag(_cfg(reranker_enabled=False, cot_enabled=True))
    assert isinstance(cot_only, CoTObservabilityRetriever)
    assert cot_only.reranker is None

    _, rerank_only = build_rag(_cfg(reranker_enabled=True, cot_enabled=False))
    assert type(rerank_only) is ObservabilityRetriever
    assert isinstance(rerank_only.reranker, DomainReranker)


@pytest.mark.unit
def test_build_reranker_gets_criticality_map_from_config():
    rr = build_reranker(_cfg(service_criticality_map={"svc-a": 0.2, "svc-b": 0.9}))
    assert rr is not None
    assert rr.service_criticality_map == {"svc-a": 0.2, "svc-b": 0.9}
    assert build_reranker(_cfg(reranker_enabled=False)) is None


# ── CoT: bug latente de assinatura/duplo rerank ──────────────────────────────


class _FakeIndexer:
    index = None
    model = None


@pytest.mark.unit
def test_cot_passes_reranker_to_base_and_reranks_once(monkeypatch):
    """Antes: rerank(query, raw) → AttributeError; e rerank 2× por busca."""
    reranker = MagicMock(spec=DomainReranker)
    reranker.rerank.return_value = []
    retriever = CoTObservabilityRetriever(_FakeIndexer(), reranker=reranker)

    # reranker vive na base (ObservabilityRetriever.reranker)
    assert retriever.reranker is reranker

    # Com index None, search() retorna [] antes de reranquear → rerank não é
    # chamado; o ponto é que não explode e o fluxo é o de search().
    results = retriever.search_with_cot("payment-svc timeout", top_k=3)
    assert results == []
    assert retriever.current_iteration == 1
    reranker.rerank.assert_not_called()


@pytest.mark.unit
def test_cot_with_results_reranks_exactly_once_with_correct_signature():
    import numpy as np

    from rag.indexer import IndexedChunk

    chunk = IndexedChunk(text="pool exhausted", source="log", metadata={})
    fake_index = MagicMock()
    fake_index.ntotal = 1
    fake_index.search.return_value = (np.array([[0.9]]), np.array([[0]]))
    indexer = MagicMock()
    indexer.index = fake_index
    indexer.model.encode.return_value = [np.zeros(4, dtype=np.float32)]
    indexer.chunks = [chunk]
    indexer._faiss_to_chunks = [[0]]

    reranker = MagicMock(spec=DomainReranker)
    reranker.rerank.side_effect = lambda results, query, top_k=10, **kw: results[:top_k]

    retriever = CoTObservabilityRetriever(indexer, reranker=reranker)
    out = retriever.search_with_cot("db timeout", top_k=5)

    assert len(out) == 1 and out[0].chunk is chunk
    reranker.rerank.assert_called_once()
    args, kwargs = reranker.rerank.call_args
    assert isinstance(args[0], list)  # results primeiro…
    assert isinstance(args[1], str)  # …query depois (assinatura real)
    assert kwargs.get("top_k") == 5


# ── histórico ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_history_retriever_accepts_reranker():
    rr = DomainReranker()
    r = ObservabilityRetrieverWithHistory(
        _FakeIndexer(), db_path=":memory:", reranker=rr
    )
    assert r.reranker is rr
    # compat: sem reranker continua funcionando
    assert (
        ObservabilityRetrieverWithHistory(_FakeIndexer(), db_path=":memory:").reranker
        is None
    )
