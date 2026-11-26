"""Smoke real-RAG: carrega modelos reais (SentenceTransformer, FAISS, CrossEncoder).

Fecha o ponto cego do bug `num_workers`: os testes unit mockam o modelo e
absorvem kwargs inválidos; aqui o encode roda de verdade. Sem torch/faiss
instalados (job unit) o módulo inteiro é pulado via importorskip.

Marcadores: `real_rag` + `slow` (seleção por `-m real_rag`) e `integration`
(o gate de coleta em tests/conftest.py exige um marcador de nível; não há
`real_rag` em TIER_MARKERS e o write-set da tarefa A1 exclui o conftest).
"""

from __future__ import annotations

import importlib.util
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

# find_spec em vez de importorskip: importorskip importaria torch na coleta e o
# gate de tests/conftest.py (AC #4) proíbe módulo ML carregado durante a coleta.
_MISSING = [
    m for m in ("sentence_transformers", "faiss") if importlib.util.find_spec(m) is None
]

# macOS: torch e faiss carregam libomp duplicado e faiss.search aborta o
# processo. No runner Linux é inócuo. Definido antes de qualquer import de ML
# (todos lazy, dentro das fixtures/testes) e herdado pelo subprocess da CLI.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

pytestmark = [
    pytest.mark.real_rag,
    pytest.mark.slow,
    pytest.mark.integration,
    pytest.mark.skipif(bool(_MISSING), reason=f"stack ML ausente: {_MISSING}"),
]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_QUERY = "connection pool exhausted payment"


@pytest.fixture(scope="module")
def report():
    """AggregatedReport do incidente semente payment-pool-exhaustion (seed 42)."""
    from config.loader import AppConfig
    from data.sample_incident import create_incident
    from pipeline.aggregator import Aggregator

    random.seed(42)
    incident = create_incident()
    t = AppConfig().thresholds
    return Aggregator(
        sigma_threshold=t.sigma_threshold,
        baseline_ratio=t.baseline_ratio,
        percentile_threshold=t.percentile_threshold,
        max_hops=t.max_hops,
    ).process(incident.logs, incident.metrics, incident.traces)


@pytest.fixture(scope="module")
def indexer(report):
    from rag.indexer import ObservabilityIndexer

    idx = ObservabilityIndexer()
    idx.build_index(report.log_templates, report.metric_anomalies, report.slow_traces)
    return idx


def test_load_model_real_dim384():
    from rag.indexer import ObservabilityIndexer

    idx = ObservabilityIndexer()
    idx._load_model()
    assert idx.model is not None
    vec = idx.model.encode(["connection pool exhausted"], normalize_embeddings=True)
    assert vec.shape == (1, 384)


def test_build_index_real_ntotal(report, indexer):
    assert report.log_templates, "incidente semente sem templates de log"
    assert indexer.index is not None
    assert indexer.index.ntotal > 0
    assert indexer.index.d == 384
    assert len(indexer.chunks) >= indexer.index.ntotal


def test_search_relevance_payment_pool_top3(indexer):
    from rag.retriever import ObservabilityRetriever

    results = ObservabilityRetriever(indexer).search(_QUERY, top_k=5)
    assert results, "busca real não retornou nada"
    top3 = " ".join(r.chunk.text.lower() for r in results[:3])
    assert "pool" in top3, f"padrão payment-pool fora do top-3: {top3[:300]}"


def test_save_load_roundtrip_and_stale_hash(indexer, tmp_path):
    from rag.indexer import ObservabilityIndexer
    from rag.retriever import ObservabilityRetriever

    cache = str(tmp_path / "rag-cache")
    indexer.save_index(cache, content_hash="hash-a")

    fresh = ObservabilityIndexer()
    assert fresh.load_index(cache, content_hash="hash-a") is True
    fresh._load_model()
    assert fresh.index.ntotal == indexer.index.ntotal

    before = ObservabilityRetriever(indexer).search(_QUERY, top_k=5)
    after = ObservabilityRetriever(fresh).search(_QUERY, top_k=5)
    assert [r.chunk.text for r in after] == [r.chunk.text for r in before]

    stale = ObservabilityIndexer()
    assert stale.load_index(cache, content_hash="hash-b") is False


def test_cross_encoder_real_loads_and_reranks(indexer):
    from rag.reranker import DomainReranker
    from rag.retriever import ObservabilityRetriever

    reranker = DomainReranker(use_cross_encoder=True)
    # Não degradou para Tier 1: o CrossEncoder real carregou.
    assert reranker.use_cross_encoder is True
    assert reranker._cross_encoder is not None

    results = ObservabilityRetriever(indexer).search(
        _QUERY, top_k=20, use_reranking=False
    )
    ranked = reranker.rerank(results, _QUERY, top_k=5, use_tier2=True)
    assert 0 < len(ranked) <= 5
    scores = [r.score for r in ranked]
    assert scores == sorted(scores, reverse=True)

    # Predição real do cross-encoder (o caminho que um mock não exercita).
    pred = reranker._cross_encoder.predict([(_QUERY, ranked[0].chunk.text)])
    assert len(pred) == 1


def test_cli_rebuild_rag_smoke(tmp_path):
    """`main.py --no-llm --rebuild-rag`: exit ∈ {0,3} (3 = aguarda aprovação)."""
    out = tmp_path / "out"
    env = {**os.environ, "PYTHONPATH": str(_REPO_ROOT)}
    proc = subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "main.py"),
            "--no-llm",
            "--rebuild-rag",
            "--output-dir",
            str(out),
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode in (0, 3), proc.stdout[-1500:] + proc.stderr[-1500:]
    assert out.is_dir() and any(out.rglob("*.md")), "postmortem não gerado em tmp_path"
