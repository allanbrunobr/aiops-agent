"""Índice RAG carregado do cache devolve o MESMO chunk que o índice recém-construído.

P2 — B4/F06 (docs/analise-20261109.md): o FAISS guarda só textos únicos e os
chunks têm duplicatas (o texto de métrica omite o serviço). build_index cria
`_faiss_to_chunks`, mas load_index não o recriava; o retriever caía em
`chunks[idx]` e, com uma duplicata antes, devolvia o chunk errado ao LLM.

Usa um modelo de embedding falso e determinístico e um FAISS mínimo em numpy
(IndexFlatIP exato + write/read_index): o projeto proíbe carregar ML nos testes,
e o defeito está no mapeamento posição → chunk, não no FAISS.
"""
from __future__ import annotations

import hashlib
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import numpy as np

from rag.indexer import ObservabilityIndexer
from rag.retriever import ObservabilityRetriever


class _FlatIP:
    """IndexFlatIP exato em numpy: add, search por produto interno, ntotal."""

    def __init__(self, dim):
        self.dim = dim
        self.vectors = np.zeros((0, dim), dtype=np.float32)

    @property
    def ntotal(self):
        return len(self.vectors)

    def add(self, vecs):
        self.vectors = np.vstack([self.vectors, np.asarray(vecs, dtype=np.float32)])

    def search(self, query, k):
        scores = np.asarray(query, dtype=np.float32) @ self.vectors.T
        order = np.argsort(-scores, axis=1)[:, :k]
        return np.take_along_axis(scores, order, axis=1), order


def _write_index(index, path):
    with open(path, "wb") as fh:
        np.save(fh, index.vectors)


def _read_index(path):
    with open(path, "rb") as fh:
        vectors = np.load(fh)
    index = _FlatIP(vectors.shape[1])
    index.add(vectors)
    return index


@pytest.fixture(autouse=True)
def _fake_faiss():
    fake = SimpleNamespace(IndexFlatIP=_FlatIP, write_index=_write_index, read_index=_read_index)
    with patch.dict(sys.modules, {"faiss": fake}):
        yield

DIM = 32


class _HashModel:
    """Embedding determinístico: texto igual → vetor igual (cosseno 1)."""

    def encode(self, texts, normalize_embeddings=True, **_):
        rows = []
        for t in texts:
            seed = int(hashlib.sha256(t.encode()).hexdigest()[:8], 16)
            v = np.random.default_rng(seed).standard_normal(DIM).astype(np.float32)
            rows.append(v / np.linalg.norm(v))
        return np.array(rows, dtype=np.float32)


def _data():
    logs = [
        SimpleNamespace(template="db connection refused", level="ERROR", count=3,
                        services={"payment-svc"}),
        SimpleNamespace(template="pool exhausted", level="ERROR", count=2,
                        services={"order-svc"}),
    ]
    # Mesmo metric_name/anomaly_type em serviços diferentes → mesmo texto (duplicata).
    metrics = [
        SimpleNamespace(metric_name="latency_p99", anomaly_type="spike",
                        service="svc-1", deviation_sigma=4.0),
        SimpleNamespace(metric_name="latency_p99", anomaly_type="spike",
                        service="svc-2", deviation_sigma=5.0),
    ]
    traces = [SimpleNamespace(path=["gateway", "payment-svc"], total_duration_ms=900,
                              bottleneck_service="payment-svc")]
    return logs, metrics, traces


def _indexer() -> ObservabilityIndexer:
    idx = ObservabilityIndexer()
    idx.model = _HashModel()
    return idx


@pytest.mark.unit
class TestCachedIndexMapping:
    def test_cached_index_returns_same_chunk_as_fresh_index(self, tmp_path):
        trace_query = "slow trace gateway -> payment-svc duration bottleneck payment-svc"
        with patch.object(ObservabilityIndexer, "_load_model", lambda self: None):
            fresh = _indexer()
            fresh.build_index(*_data())
            fresh.save_index(str(tmp_path), "hash-1")

            cached = _indexer()
            assert cached.load_index(str(tmp_path), "hash-1") is True

        fresh_hit = ObservabilityRetriever(fresh).search(trace_query, top_k=1)[0]
        cached_hit = ObservabilityRetriever(cached).search(trace_query, top_k=1)[0]

        assert fresh_hit.chunk.source == "trace"
        assert cached_hit.chunk.source == "trace"
        assert cached_hit.chunk.text == fresh_hit.chunk.text

    def test_all_queries_agree_between_fresh_and_cached(self, tmp_path):
        with patch.object(ObservabilityIndexer, "_load_model", lambda self: None):
            fresh = _indexer()
            fresh.build_index(*_data())
            fresh.save_index(str(tmp_path), "hash-1")
            cached = _indexer()
            cached.load_index(str(tmp_path), "hash-1")

        for chunk in fresh.chunks:
            a = ObservabilityRetriever(fresh).search(chunk.text, top_k=1)[0].chunk
            b = ObservabilityRetriever(cached).search(chunk.text, top_k=1)[0].chunk
            assert (a.source, a.text) == (b.source, b.text)

    def test_mapping_size_mismatch_invalidates_cache(self, tmp_path):
        with patch.object(ObservabilityIndexer, "_load_model", lambda self: None):
            fresh = _indexer()
            fresh.build_index(*_data())
            fresh.save_index(str(tmp_path), "hash-1")
        # Corrompe o cache: um chunk a mais com texto novo → mapeamento ≠ ntotal.
        import json
        chunks_path = tmp_path / "chunks.json"
        data = json.loads(chunks_path.read_text())
        data.append({"text": "extra unindexed text", "source": "log", "metadata": {}})
        chunks_path.write_text(json.dumps(data))

        cached = _indexer()
        assert cached.load_index(str(tmp_path), "hash-1") is False
        assert cached.index is None
