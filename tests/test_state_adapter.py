"""P1 (F5-4): adapter IncidentState -> result V0-shaped + captura estruturada.

Cobre as 6 seções que o renderer rico exige: root_cause, timeline,
mitigation.action(+pattern_matched), safety_validation, remediation_validation
e verification. Roda o engine V1 --no-llm de ponta a ponta (sem rede, sem I/O
em output/).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from application.use_cases.state_adapter import state_to_result
from blueprint.engine import BlueprintEngine
from blueprint.pipelines.incident_response import build_incident_response_pipeline
from config.loader import AppConfig
from domain.alert import NormalizedAlert
from domain.incident_state import IncidentState
from safety.tnr import MitigationAction

pytestmark = pytest.mark.unit

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _initial_state() -> IncidentState:
    from data.sample_incident import create_incident

    incident = create_incident()
    return IncidentState(
        incident_id="inc-p1",
        correlation_id="inc-p1",
        received_at=_TS,
        started_at=_TS,
        alert=NormalizedAlert(
            source="test",
            source_id="p1",
            correlation_id="inc-p1",
            title="Test",
            description="P1 adapter",
            severity="error",
            alert_type="TestAlert",
            timestamp=_TS,
            detection_timestamp=_TS,
        ),
        no_llm=True,
        no_rag=True,
        metadata={
            "raw_incident_data": {
                "logs": incident.logs,
                "metrics": incident.metrics,
                "traces": incident.traces,
            },
        },
    )


@pytest.fixture(scope="module")
def final_state() -> IncidentState:
    config = AppConfig()
    return BlueprintEngine(config).run(
        build_incident_response_pipeline(config), _initial_state()
    )


@pytest.fixture(scope="module")
def result(final_state: IncidentState) -> dict:
    return state_to_result(final_state)


def test_root_cause_section(result: dict, final_state: IncidentState) -> None:
    diagnosis = result["stages"]["diagnosis"]
    expected = final_state.diagnosis or {}
    assert diagnosis["root_cause"] == expected["root_cause"]
    assert diagnosis["confidence"] == expected["confidence"]


def test_timeline_has_state_machine_transitions(result: dict) -> None:
    timeline = result["timeline"]
    assert timeline, "V1 não registra transições — renderer fica sem State Machine"
    for entry in timeline:
        assert set(entry) == {"time", "from", "to", "reason"}
    tos = [e["to"] for e in timeline]
    assert "DIAGNOSING" in tos and "VERIFYING" in tos


def test_mitigation_action_is_object_with_pattern(
    result: dict, final_state: IncidentState
) -> None:
    mitigation = result["stages"]["mitigation"]
    action = mitigation["action"]
    assert isinstance(action, MitigationAction)
    proposed = final_state.proposed_action or {}
    assert action.description == proposed["description"]
    assert action.command == proposed["command"]
    assert action.risk_level == proposed["risk_level"]
    assert action.rollback_command == proposed["rollback"]
    assert mitigation["pattern_matched"]  # não-vazio: alimenta Action Items


def test_safety_validation_is_structured_dict(result: dict) -> None:
    sv = result["stages"]["safety_validation"]
    assert {
        "risk_level",
        "risk_score",
        "approval_status",
        "requires_human",
        "blast_radius_scope",
        "recommendation",
    } <= set(sv)
    assert isinstance(sv["risk_score"], (int, float))


def test_remediation_validation_is_structured_dict(result: dict) -> None:
    rv = result["stages"]["remediation_validation"]
    assert {"risk", "is_safe", "blocked_patterns", "warnings", "recommendation"} <= set(
        rv
    )
    assert isinstance(rv["is_safe"], bool)
    assert isinstance(rv["blocked_patterns"], list)


def test_verification_is_explicit_not_verified(result: dict) -> None:
    # Sem snapshots reais de saúde, V1 não pode afirmar sucesso: registra
    # not_verified em vez de omitir a seção (measured-only, igual ao V0).
    ver = result["stages"]["verification"]
    assert ver["result"] == "not_verified"
    assert {"result", "health_delta", "message"} <= set(ver)


def test_safety_decision_follows_verdict(
    result: dict, final_state: IncidentState
) -> None:
    decision = result["stages"]["safety_decision"]
    assert decision["requires_human"] == (final_state.safety_verdict == "NEEDS_REVIEW")


def test_adapter_renders_all_rich_sections(result: dict) -> None:
    from application.use_cases.postmortem import render_postmortem_markdown
    from data.sample_incident import create_incident

    md = render_postmortem_markdown(result, create_incident(), now=_TS)
    for heading in (
        "Root Cause",
        "State Machine Transitions",
        "Mitigation Proposed",
        "Pre-Execution Safety Check",
        "Remediation",
        "TNR Verification",
    ):
        assert heading in md, f"seção ausente: {heading}"


def test_adapter_tolerates_bare_state() -> None:
    """Estado sem nenhuma captura (ex.: abort cedo) não quebra o adapter."""
    out = state_to_result(_initial_state())
    assert out["stages"]["diagnosis"] == {}
    assert out["timeline"] == []
    assert out["stages"]["mitigation"] == {}
    assert out["success"] is False
