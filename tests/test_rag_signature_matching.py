"""
Testes unitários para query_similar_signatures em rag/memory.py.

Verifica busca de assinaturas históricas similares por similaridade de Jaccard
entre nomes de classe dos sinais. Inclui cold start e tratamento de exceções.
"""

from __future__ import annotations

import pytest

from rag.memory import IncidentMemoryDB


class _CpuSpike:
    """Sinal fictício — pico de CPU."""


class _MemoryLeak:
    """Sinal fictício — vazamento de memória."""


class _DiskIO:
    """Sinal fictício — I/O de disco."""


@pytest.fixture()
def tmp_db(tmp_path):
    """IncidentMemoryDB em arquivo temporário isolado."""
    return IncidentMemoryDB(db_path=str(tmp_path / "sig_match.db"))


# ---- cold start ----

def test_cold_start_returns_empty(tmp_db):
    """Sem assinaturas armazenadas, deve retornar lista vazia."""
    results = tmp_db.query_similar_signatures([_CpuSpike()])
    assert results == []


def test_cold_start_no_exception(tmp_db):
    """Cold start não deve lançar nenhuma exceção."""
    try:
        tmp_db.query_similar_signatures([])
    except Exception as exc:
        pytest.fail(f"query_similar_signatures lançou exceção no cold start: {exc}")


# ---- com assinaturas armazenadas ----

def test_returns_matches_when_signatures_stored(tmp_db):
    """Com assinaturas armazenadas, deve retornar ao menos uma correspondência."""
    signals = [_CpuSpike(), _MemoryLeak()]
    tmp_db.store_degradation_signature(signals, risk_score=60.0, incident_id="inc-A")

    results = tmp_db.query_similar_signatures(signals)
    assert len(results) >= 1
    assert results[0]["incident_id"] == "inc-A"
    assert results[0]["similarity_score"] > 0.5


def test_top_k_limits_results(tmp_db):
    """top_k deve limitar número de resultados retornados."""
    signals = [_CpuSpike(), _MemoryLeak()]
    for i in range(5):
        tmp_db.store_degradation_signature(
            signals, risk_score=40.0 + i, incident_id=f"inc-{i}"
        )

    results = tmp_db.query_similar_signatures(signals, top_k=2)
    assert len(results) <= 2


def test_result_structure(tmp_db):
    """Cada resultado deve ter incident_id, similarity_score e outcome."""
    signals = [_CpuSpike()]
    tmp_db.store_degradation_signature(signals, risk_score=35.0, incident_id="inc-struct")

    results = tmp_db.query_similar_signatures(signals)
    assert results, "Deve retornar ao menos um resultado"
    r = results[0]
    assert "incident_id" in r
    assert "similarity_score" in r
    assert "outcome" in r
    assert isinstance(r["similarity_score"], float)


def test_different_signals_lower_similarity(tmp_db):
    """Sinais completamente diferentes devem ter similaridade 0.0."""
    tmp_db.store_degradation_signature(
        [_CpuSpike()], risk_score=50.0, incident_id="inc-cpu"
    )

    # Consulta com sinal totalmente diferente
    results = tmp_db.query_similar_signatures([_DiskIO()])
    if results:
        # Pode retornar mas similaridade deve ser 0 (sem interseção)
        assert results[0]["similarity_score"] == 0.0


def test_identical_signals_high_similarity(tmp_db):
    """Sinais idênticos devem ter similaridade 1.0."""
    signals = [_CpuSpike(), _MemoryLeak()]
    tmp_db.store_degradation_signature(signals, risk_score=75.0, incident_id="inc-high")

    results = tmp_db.query_similar_signatures(signals)
    assert results
    assert results[0]["similarity_score"] == pytest.approx(1.0)


# ---- tratamento de exceção ----

def test_exception_returns_empty_list(tmp_db, monkeypatch):
    """Exceção durante a query deve retornar [] sem propagar."""
    def _bad_connect():
        raise RuntimeError("DB unreachable")

    monkeypatch.setattr(tmp_db, "_connect", _bad_connect)
    results = tmp_db.query_similar_signatures([_CpuSpike()])
    assert results == []
