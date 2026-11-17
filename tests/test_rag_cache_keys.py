"""F6-3: caches em memória do RAG têm o modelo na chave e um teto.

Antes ``_query_cache`` era keyed só por md5(query) e ``_embedding_cache`` só
pelo texto: trocar ``model_name`` devolvia vetores do modelo anterior; e ambos
cresciam sem limite durante a vida do processo. (O cache em DISCO já validava
``model_name`` — o problema era só em memória.)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import rag.indexer as idx
import rag.retriever as rt
from rag.indexer import IndexedChunk, ObservabilityIndexer
from rag.retriever import ObservabilityRetriever


def _indexer(model_name: str, vec: float) -> MagicMock:
    m = MagicMock()
    m.model_name = model_name
    m.model.encode.side_effect = lambda texts, **kw: [np.full(4, vec, dtype=np.float32)]
    m.index.ntotal = 1
    m.index.search.return_value = (np.array([[0.9]]), np.array([[0]]))
    m.chunks = [IndexedChunk(text="t", source="log", metadata={})]
    m._faiss_to_chunks = [[0]]
    return m


@pytest.mark.unit
def test_query_cache_is_keyed_by_model_name():
    with patch.dict(rt._query_cache, {}, clear=True):
        a = _indexer("model-a", 1.0)
        b = _indexer("model-b", 2.0)
        ObservabilityRetriever(a).search("same query", top_k=1, use_reranking=False)
        ObservabilityRetriever(b).search("same query", top_k=1, use_reranking=False)

        keys = list(rt._query_cache)
        assert len(keys) == 2 and {k[0] for k in keys} == {"model-a", "model-b"}
        # cada modelo codificou a própria query (não reaproveitou o vetor do outro)
        a.model.encode.assert_called_once()
        b.model.encode.assert_called_once()


@pytest.mark.unit
def test_query_cache_is_bounded(monkeypatch):
    monkeypatch.setattr(rt, "_QUERY_CACHE_MAX", 3)
    with patch.dict(rt._query_cache, {}, clear=True):
        r = ObservabilityRetriever(_indexer("m", 1.0))
        for i in range(10):
            r.search(f"q{i}", top_k=1, use_reranking=False)
        assert len(rt._query_cache) == 3
        assert ("m", __import__("hashlib").md5(b"q9").hexdigest()) in rt._query_cache


@pytest.mark.unit
def test_embedding_cache_is_keyed_by_model_name():
    from pipeline.log_parser import LogTemplate

    tpl = LogTemplate(template="pool exhausted", count=1, level="ERROR")
    tpl.services = {"svc"}

    fake_faiss = MagicMock()
    fake_faiss.IndexFlatIP.return_value = MagicMock()
    fake_faiss.IndexIVFFlat.return_value = MagicMock()

    def encode(texts, **kw):
        return np.ones((len(texts), 4), dtype=np.float32)

    with (
        patch.dict(idx._embedding_cache, {}, clear=True),
        patch.dict("sys.modules", {"faiss": fake_faiss}),
    ):
        for name in ("model-a", "model-b"):
            ix = ObservabilityIndexer(model_name=name)
            ix.model = MagicMock()
            ix.model.encode.side_effect = encode
            ix._load_model = lambda: None
            ix.build_index([tpl], [], [])

        models = {k[0] for k in idx._embedding_cache}
        assert models == {"model-a", "model-b"}
        assert all(isinstance(k, tuple) and len(k) == 2 for k in idx._embedding_cache)


@pytest.mark.unit
def test_embedding_cache_is_bounded(monkeypatch):
    from pipeline.log_parser import LogTemplate

    monkeypatch.setattr(idx, "_EMBEDDING_CACHE_MAX", 2)
    tpls = []
    for i in range(5):
        t = LogTemplate(template=f"tpl {i}", count=1, level="INFO")
        t.services = {"s"}
        tpls.append(t)

    fake_faiss = MagicMock()
    with (
        patch.dict(idx._embedding_cache, {}, clear=True),
        patch.dict("sys.modules", {"faiss": fake_faiss}),
    ):
        ix = ObservabilityIndexer(model_name="m")
        ix.model = MagicMock()
        ix.model.encode.side_effect = lambda texts, **kw: np.ones(
            (len(texts), 4), dtype=np.float32
        )
        ix._load_model = lambda: None
        ix.build_index(tpls, [], [])
        # o lote inteiro (5) foi indexado; o cache ficou no teto (2) após a evicção
        assert ix.index is not None
        assert len(idx._embedding_cache) == 2
