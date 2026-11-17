"""Regressão F2-1: as safety layers do V1 realmente validam a ação proposta.

Antes do fix, nenhum código setava state.proposed_action (nem o diagnoser
node nem o fallback heurístico) → version_validator/pre_validator/tnr
skipavam com 'no_proposed_action', contradizendo o invariante
"ALL 4 safety layers execute" de blueprint/pipelines/incident_response.py.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from blueprint.engine import BlueprintEngine, PipelineAborted
from blueprint.nodes import NodeType
from blueprint.pipelines.incident_response import build_incident_response_pipeline
from config.loader import AppConfig
from domain.alert import NormalizedAlert
from domain.incident_state import IncidentState


def _make_state(incident) -> IncidentState:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return IncidentState(
        incident_id="inc-f21-test",
        correlation_id="inc-f21-test",
        received_at=now,
        started_at=now,
        alert=NormalizedAlert(
            source="test",
            source_id="s1",
            correlation_id="inc-f21-test",
            title="Test",
            description="F2-1 wiring test",
            severity="critical",
            alert_type="HighErrorRate",
            timestamp=now,
            detection_timestamp=now,
        ),
        no_llm=True,
        no_rag=True,
        metadata={
            "raw_incident_data": {
                "logs": incident.logs,
                "metrics": incident.metrics,
                "traces": incident.traces,
            }
        },
    )


@pytest.mark.unit
def test_diagnoser_fallback_sets_proposed_action():
    """Fallback heurístico (modo --no-llm) deve propor ação via MitigationAgent."""
    from blueprint.adapters.pipeline_nodes import (
        make_aggregator_node,
        make_diagnoser_fallback,
    )
    from data.sample_incident import create_incident

    config = AppConfig()
    state = _make_state(create_incident())
    state = make_aggregator_node(config)(state)
    state = make_diagnoser_fallback(config)(state)

    assert state.proposed_action is not None
    assert state.proposed_action["command"]
    assert state.proposed_action["description"]
    assert "risk_level" in state.proposed_action
    assert "rollback" in state.proposed_action


@pytest.mark.unit
def test_safety_layers_validate_real_proposed_action():
    """Pipeline completo V1 (--no-llm): camadas executam de verdade, sem skip."""
    from data.sample_incident import create_incident

    config = AppConfig()
    state = _make_state(create_incident())
    pipeline = build_incident_response_pipeline(config)
    engine = BlueprintEngine(config=config)

    final = engine.run(pipeline, state)

    assert final.proposed_action is not None
    reasons = " | ".join(final.safety_reasons)
    assert "VersionValidator:" in reasons, (
        "version_validator skipou (no_proposed_action?)"
    )
    assert "PreValidator:" in reasons, "pre_validator skipou (no_proposed_action?)"
    assert final.safety_verdict in ("APPROVED", "NEEDS_REVIEW")


@pytest.mark.unit
def test_dangerous_proposed_action_aborts_pipeline():
    """E2E V1: ação proposta perigosa → pipeline BLOQUEIA (PipelineAborted)."""
    from data.sample_incident import create_incident

    config = AppConfig()
    state = _make_state(create_incident())
    pipeline = build_incident_response_pipeline(config)

    dangerous = {
        "description": "Delete all pods in production",
        "command": "kubectl delete pods --all -n production && rm -fr /",
        "risk_level": "HIGH",
        "rollback": None,
    }

    def inject(state: IncidentState) -> IncidentState:
        return dataclasses.replace(
            state,
            diagnosis={
                "root_cause": "test",
                "affected_services": ["payment-svc"],
                "confidence": "high",
            },
            proposed_action=dangerous,
        )

    nodes = [
        dataclasses.replace(n, execute=inject, node_type=NodeType.DETERMINISTIC)
        if n.id == "diagnoser"
        else n
        for n in pipeline.nodes
    ]
    pipeline = dataclasses.replace(pipeline, nodes=nodes)

    engine = BlueprintEngine(config=config)
    with pytest.raises(PipelineAborted):
        engine.run(pipeline, state)

    last = engine.results[-1]
    assert last.success is False
    assert last.node_id in ("version_validator", "pre_validator")
