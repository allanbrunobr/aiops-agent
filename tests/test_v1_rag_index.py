"""Regressão F2-2: RAG do V1 nasce vazio (build_rag sem build_index).

build_rag() cria o indexer sem índice; nenhum node do V1 indexava o report
agregado → retriever.search() sempre retornava [] (index is None).
O índice deve ser construído a partir do report do incidente corrente.
"""

from __future__ import annotations

import dataclasses
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from config.loader import AppConfig
from domain.alert import NormalizedAlert
from domain.incident_state import IncidentState


def _make_state(incident, retriever) -> IncidentState:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return IncidentState(
        incident_id="inc-f22-test",
        correlation_id="inc-f22-test",
        received_at=now,
        started_at=now,
        alert=NormalizedAlert(
            source="test",
            source_id="s1",
            correlation_id="inc-f22-test",
            title="Test",
            description="F2-2 RAG index test",
            severity="critical",
            alert_type="HighErrorRate",
            timestamp=now,
            detection_timestamp=now,
        ),
        no_llm=True,
        no_rag=False,
        metadata={
            "raw_incident_data": {
                "logs": incident.logs,
                "metrics": incident.metrics,
                "traces": incident.traces,
            },
            "_retriever": retriever,
        },
    )


@pytest.mark.unit
def test_v1_builds_rag_index_from_current_report():
    """V1 com RAG habilitado → retriever retorna chunks do incidente corrente."""
    import numpy as np

    from app.builders import build_rag
    from blueprint.adapters.pipeline_nodes import (
        make_aggregator_node,
        make_diagnoser_fallback,
    )
    from data.sample_incident import create_incident

    faiss_mock = MagicMock()
    fake_index = MagicMock()
    fake_index.ntotal = 1000
    fake_index.search.return_value = (
        np.array([[0.9, 0.8, 0.7]], dtype=np.float32),
        np.array([[0, 1, 2]]),
    )
    faiss_mock.IndexFlatIP.return_value = fake_index
    faiss_mock.IndexIVFFlat.return_value = fake_index

    with patch.dict(sys.modules, {"faiss": faiss_mock}):
        indexer, retriever = build_rag()
        fake_model = MagicMock()
        fake_model.encode.side_effect = lambda texts, **kw: np.ones(
            (len(texts), 4), dtype=np.float32
        )
        indexer._load_model = lambda: setattr(indexer, "model", fake_model)

        config = AppConfig()
        state = _make_state(create_incident(), retriever)

        # Antes do processamento, o índice não existe (build_rag não indexa)
        assert indexer.index is None

        state = make_aggregator_node(config)(state)
        state = make_diagnoser_fallback(config)(state)

        # Após o node com o report disponível, o índice deve existir
        assert indexer.index is not None
        results = retriever.search("connection pool timeout", top_k=3)
        assert results, "retriever do V1 retornou vazio com RAG habilitado"
        assert all(r.chunk.text for r in results)


@pytest.mark.unit
def test_v1_no_rag_skips_index_build():
    """Com no_rag=True, nenhum índice é construído."""
    from app.builders import build_rag
    from blueprint.adapters.pipeline_nodes import (
        make_aggregator_node,
        make_diagnoser_fallback,
    )
    from data.sample_incident import create_incident

    indexer, retriever = build_rag()
    config = AppConfig()
    state = dataclasses.replace(_make_state(create_incident(), retriever), no_rag=True)

    state = make_aggregator_node(config)(state)
    state = make_diagnoser_fallback(config)(state)

    assert indexer.index is None
