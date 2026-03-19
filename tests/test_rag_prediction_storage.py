"""
Testes unitários para store_degradation_signature em rag/memory.py.

Verifica que assinaturas de degradação pré-incidente são persistidas
corretamente e que o limiar de risk_score < 30 é respeitado.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from rag.memory import IncidentMemoryDB


class _FakeSignal:
    """Sinal fictício para testes."""


class _AnotherSignal:
    """Segundo tipo de sinal fictício."""


@pytest.fixture()
def tmp_db(tmp_path):
    """IncidentMemoryDB em arquivo temporário isolado."""
    db_path = str(tmp_path / "test_signatures.db")
    return IncidentMemoryDB(db_path=db_path)


# ---- store_degradation_signature ----

def test_store_stores_successfully(tmp_db):
    """Assinatura com risk_score >= 30 deve ser persistida sem erros."""
    signals = [_FakeSignal(), _AnotherSignal()]
    tmp_db.store_degradation_signature(signals, risk_score=45.0, incident_id="inc-001")

    # Verifica via query_similar_signatures que foi armazenada
    results = tmp_db.query_similar_signatures(signals)
    assert len(results) >= 1
    ids = [r["incident_id"] for r in results]
    assert "inc-001" in ids


def test_store_skips_below_threshold(tmp_db):
    """Assinatura com risk_score < 30 não deve ser armazenada."""
    signals = [_FakeSignal()]
    tmp_db.store_degradation_signature(signals, risk_score=29.9, incident_id="inc-low")

    results = tmp_db.query_similar_signatures(signals)
    ids = [r["incident_id"] for r in results]
    assert "inc-low" not in ids


def test_store_at_boundary_threshold(tmp_db):
    """risk_score exatamente 30 deve ser armazenado (limiar inclusivo)."""
    signals = [_FakeSignal()]
    tmp_db.store_degradation_signature(signals, risk_score=30.0, incident_id="inc-boundary")

    results = tmp_db.query_similar_signatures(signals)
    ids = [r["incident_id"] for r in results]
    assert "inc-boundary" in ids


def test_store_does_not_affect_existing_incidents(tmp_db):
    """store_degradation_signature não deve alterar registros de incidentes existentes."""
    from rag.memory import IncidentRecord

    record = IncidentRecord.new(
        scenario="test-scenario",
        root_cause="connection pool exhaustion",
        affected_services=["payment-svc"],
        diagnosis_confidence=0.9,
        resolution_time_sec=120.0,
        tags=["connection-pool"],
    )
    tmp_db.save_incident(record)

    signals = [_FakeSignal()]
    tmp_db.store_degradation_signature(signals, risk_score=55.0, incident_id="inc-new")

    # Incidente original ainda deve estar presente
    found = tmp_db.find_similar_incidents(["payment-svc"], ["connection-pool"])
    assert any(r.incident_id == record.incident_id for r in found)


def test_store_exception_propagates_to_pipeline_wrapper(tmp_db, monkeypatch):
    """Exceção interna em store_degradation_signature propaga ao caller.

    O pipeline (step_predict em main.py) é quem envolve a chamada em try/except.
    A própria função store não suprime erros de infraestrutura.
    """
    def _bad_connect():
        raise RuntimeError("simulated DB error")

    monkeypatch.setattr(tmp_db, "_connect", _bad_connect)

    # Deve propagar — o wrapper do pipeline absorve
    with pytest.raises(RuntimeError, match="simulated DB error"):
        tmp_db.store_degradation_signature([_FakeSignal()], risk_score=50.0, incident_id="inc-err")


def test_pipeline_continues_on_rag_error(tmp_db, monkeypatch):
    """Simulação de erro RAG: store não deve interromper fluxo do pipeline."""
    def _raise(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(tmp_db, "_ensure_signatures_table", _raise)

    raised = False
    try:
        tmp_db.store_degradation_signature([_FakeSignal()], risk_score=70.0, incident_id="inc-x")
    except OSError:
        raised = True

    # O caller (pipeline) deve envolver em try/except — aqui testamos a propagação
    # O teste confirma que a exceção SÓ sobe de _ensure; o wrapper em main.py a absorve.
    assert raised  # confirma que a exceção é lançada pela camada de infra (não suprimida pela store)
