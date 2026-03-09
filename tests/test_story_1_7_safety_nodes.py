"""Testes para Story 1.7 — Register Safety Layers as Blueprint Nodes.

Valida que todos os safety nodes sao DETERMINISTIC com on_failure=ABORT,
estao na ordem correta, e que SafetyBlockedError funciona.
"""
from __future__ import annotations

import pytest

from config.loader import AppConfig
from blueprint.pipelines.incident_response import build_incident_response_pipeline
from blueprint.nodes import NodeType, OnFailure


def test_pipeline_has_8_nodes():
    """Pipeline completo deve ter 8 nodes (4 pipeline + 4 safety)."""
    config = AppConfig()
    pipeline = build_incident_response_pipeline(config)
    assert len(pipeline.nodes) == 8


def test_all_safety_nodes_are_deterministic_abort():
    """Todos os safety nodes devem ser DETERMINISTIC com on_failure=ABORT."""
    config = AppConfig()
    pipeline = build_incident_response_pipeline(config)

    safety_ids = {"sanitizer", "version_validator", "pre_validator", "tnr"}
    for node in pipeline.nodes:
        if node.id in safety_ids:
            assert node.node_type == NodeType.DETERMINISTIC, (
                f"Safety node '{node.id}' must be DETERMINISTIC, got {node.node_type}"
            )
            assert node.on_failure == OnFailure.ABORT, (
                f"Safety node '{node.id}' must have on_failure=ABORT, got {node.on_failure}"
            )


def test_sanitizer_is_first_node():
    """TelemetrySanitizer DEVE ser o primeiro node do pipeline."""
    config = AppConfig()
    pipeline = build_incident_response_pipeline(config)
    assert pipeline.nodes[0].id == "sanitizer"


def test_safety_before_postmortem():
    """Todos os safety nodes devem executar antes do postmortem."""
    config = AppConfig()
    pipeline = build_incident_response_pipeline(config)
    ids = [n.id for n in pipeline.nodes]

    safety_ids = {"sanitizer", "version_validator", "pre_validator", "tnr"}
    postmortem_idx = ids.index("postmortem")

    for safety_id in safety_ids:
        safety_idx = ids.index(safety_id)
        assert safety_idx < postmortem_idx, (
            f"Safety node '{safety_id}' (index {safety_idx}) must come before "
            f"postmortem (index {postmortem_idx})"
        )


def test_pipeline_node_order_with_safety():
    """Nodes devem estar na ordem correta incluindo safety."""
    config = AppConfig()
    pipeline = build_incident_response_pipeline(config)
    ids = [n.id for n in pipeline.nodes]
    assert ids == [
        "sanitizer",
        "aggregator",
        "context_compressor",
        "diagnoser",
        "version_validator",
        "pre_validator",
        "tnr",
        "postmortem",
    ]


def test_sanitizer_node_executes():
    """Sanitizer node deve sanitizar dados brutos no estado."""
    from blueprint.adapters.safety_nodes import make_sanitizer_node
    from core.models import IncidentState, NormalizedAlert
    from data.sample_incident import create_incident

    config = AppConfig()
    adapter = make_sanitizer_node(config)

    incident = create_incident()
    state = IncidentState(
        incident_id="test-safety-001",
        correlation_id="test-safety-001",
        alert=NormalizedAlert(
            source="test", source_id="s1", correlation_id="test-safety-001",
            title="Test", severity="high",
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
    # Dados sanitizados devem estar no metadata
    assert "raw_incident_data" in result.metadata
    assert "_sanitizer" in result.metadata


def test_safety_thresholds_from_config():
    """Thresholds de safety devem vir da config."""
    config = AppConfig()
    assert config.safety.blast_radius_limit == 3
    assert config.thresholds.regression_threshold == -5.0
    assert config.safety.require_human_approval is True


def test_safety_blocked_error_import():
    """SafetyBlockedError deve ser importavel."""
    from blueprint.adapters.safety_nodes import SafetyBlockedError

    err = SafetyBlockedError("test block")
    assert str(err) == "test block"
    assert isinstance(err, Exception)


def test_full_pipeline_no_llm_with_safety():
    """Pipeline completo em modo --no-llm com safety deve funcionar."""
    from blueprint.engine import BlueprintEngine
    from core.models import IncidentState, NormalizedAlert
    from data.sample_incident import create_incident

    config = AppConfig()
    engine = BlueprintEngine(config)
    pipeline = build_incident_response_pipeline(config)

    incident = create_incident()
    state = IncidentState(
        incident_id="test-safety-full",
        correlation_id="test-safety-full",
        alert=NormalizedAlert(
            source="test", source_id="sf", correlation_id="test-safety-full",
            title="Test", severity="high",
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

    # Safety nodes executaram (sanitizer alterou metadata)
    assert "_sanitizer" in result.metadata
    # Pipeline completou (diagnosis e postmortem presentes)
    assert result.diagnosis is not None
    assert result.postmortem_md is not None
