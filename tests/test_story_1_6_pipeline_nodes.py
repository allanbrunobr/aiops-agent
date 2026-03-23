"""Testes para Story 1.6 — Register Pipeline Stages as Blueprint Nodes.

Valida pipeline definition, ordem dos nodes, tipos, on_failure,
e execucao dos adapters com dados simulados.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from config.loader import AppConfig
from blueprint.pipelines.incident_response import build_incident_response_pipeline
from blueprint.nodes import NodeType, OnFailure


def test_pipeline_has_correct_node_count():
    """Pipeline deve ter 8 nodes (4 pipeline + 4 safety)."""
    config = AppConfig()
    pipeline = build_incident_response_pipeline(config)
    assert len(pipeline.nodes) == 8


def test_pipeline_node_order():
    """Nodes devem estar na ordem correta incluindo safety."""
    config = AppConfig()
    pipeline = build_incident_response_pipeline(config)
    ids = [n.id for n in pipeline.nodes]
    assert ids == [
        "sanitizer", "aggregator", "context_compressor", "diagnoser",
        "version_validator", "pre_validator", "tnr", "postmortem",
    ]


def test_deterministic_nodes_have_abort_or_skip():
    """Nodes deterministicos devem ter on_failure=ABORT ou SKIP."""
    config = AppConfig()
    pipeline = build_incident_response_pipeline(config)
    for node in pipeline.nodes:
        if node.node_type == NodeType.DETERMINISTIC:
            assert node.on_failure in (OnFailure.ABORT, OnFailure.SKIP), (
                f"Node '{node.id}' is deterministic but has on_failure={node.on_failure}"
            )


def test_agentic_nodes_have_fallback():
    """Nodes agenticos devem ter fallback function definido."""
    config = AppConfig()
    pipeline = build_incident_response_pipeline(config)
    for node in pipeline.nodes:
        if node.node_type == NodeType.AGENTIC:
            assert node.fallback is not None, (
                f"Agentic node '{node.id}' has no fallback function"
            )


def test_aggregator_adapter_produces_report():
    """Aggregator adapter deve produzir aggregated_report no estado."""
    from blueprint.adapters.pipeline_nodes import make_aggregator_node
    from core.models import IncidentState, NormalizedAlert
    from data.sample_incident import create_incident

    config = AppConfig()
    adapter = make_aggregator_node(config)

    incident = create_incident()
    state = IncidentState(
        incident_id="test-001",
        correlation_id="test-001",
        received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        alert=NormalizedAlert(
            source="test", source_id="t1", correlation_id="test-001",
            title="Test",
            description="Teste de pipeline node",
            severity="error",
            alert_type="TestAlert",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            detection_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        metadata={
            "raw_incident_data": {
                "logs": incident.logs,
                "metrics": incident.metrics,
                "traces": incident.traces,
            },
        },
    )

    result = adapter(state)
    assert result.aggregated_report is not None
    assert result.aggregated_report["log_templates_count"] > 0
    assert result.aggregated_report["severity"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert "_report_obj" in result.aggregated_report


def test_diagnoser_fallback_produces_diagnosis():
    """Diagnoser fallback deve produzir diagnosis dict com shape correta."""
    from blueprint.adapters.pipeline_nodes import make_aggregator_node, make_diagnoser_fallback
    from core.models import IncidentState, NormalizedAlert
    from data.sample_incident import create_incident

    config = AppConfig()
    aggregator = make_aggregator_node(config)
    fallback = make_diagnoser_fallback(config)

    incident = create_incident()
    state = IncidentState(
        incident_id="test-002",
        correlation_id="test-002",
        received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        alert=NormalizedAlert(
            source="test", source_id="t2", correlation_id="test-002",
            title="Test",
            description="Teste de pipeline node",
            severity="error",
            alert_type="TestAlert",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            detection_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        metadata={
            "raw_incident_data": {
                "logs": incident.logs,
                "metrics": incident.metrics,
                "traces": incident.traces,
            },
        },
    )

    # Primeiro executa aggregator para ter report
    state = aggregator(state)
    # Depois executa fallback
    result = fallback(state)

    assert result.diagnosis is not None
    diagnosis = result.diagnosis
    assert "root_cause" in diagnosis
    assert "evidence" in diagnosis
    assert "affected_services" in diagnosis
    assert "confidence" in diagnosis
    assert "timeline" in diagnosis


def test_full_pipeline_no_llm():
    """Pipeline completo em modo --no-llm deve produzir diagnosis e postmortem."""
    from blueprint.engine import BlueprintEngine
    from core.models import IncidentState, NormalizedAlert
    from data.sample_incident import create_incident

    config = AppConfig()
    engine = BlueprintEngine(config)
    pipeline = build_incident_response_pipeline(config)

    incident = create_incident()
    state = IncidentState(
        incident_id="test-003",
        correlation_id="test-003",
        received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        alert=NormalizedAlert(
            source="test", source_id="t3", correlation_id="test-003",
            title="Test",
            description="Teste de pipeline node",
            severity="error",
            alert_type="TestAlert",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            detection_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        no_llm=True,
        metadata={
            "raw_incident_data": {
                "logs": incident.logs,
                "metrics": incident.metrics,
                "traces": incident.traces,
            },
        },
    )

    result = engine.run(pipeline, state)

    assert result.aggregated_report is not None
    assert result.diagnosis is not None
    assert result.postmortem_md is not None
    assert "Root Cause" in result.postmortem_md
