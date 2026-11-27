"""Adapter IncidentState (V1) -> dict ``result`` no formato do V0.

O renderer rico (``application.use_cases.postmortem``) consome o dict que o
engine V0 montava. Este módulo reconstrói esse dict a partir do estado e dos
dados estruturados que os nodes do blueprint guardam em ``state.metadata``:

- ``_detection``: saída do DetectorAgent (stages.detection)
- ``_timeline``: transições da máquina de estados
- ``_pattern_matched``: padrão da mitigação (alimenta Action Items)
- ``_safety_validation`` / ``_remediation_validation``: dicts dos validators
- ``_verification``: resultado do TNR

Função pura, sem I/O. Seções sem captura saem vazias — o renderer já as trata.
"""

from __future__ import annotations

from typing import Any

from domain.incident_state import IncidentState
from domain.terminal_state import TerminalOutcome, classify_terminal


def _mitigation_stage(state: IncidentState) -> dict[str, Any]:
    proposed = state.proposed_action
    if not proposed:
        return {}
    # safety.tnr: por isso o adapter vive em blueprint/adapters e não em
    # application (regra de camadas, tests/test_layer_dependencies.py).
    from safety.tnr import MitigationAction

    action = MitigationAction(
        description=proposed.get("description", ""),
        command=proposed.get("command", ""),
        risk_level=proposed.get("risk_level", "MEDIUM"),
        rollback_command=proposed.get("rollback"),
    )
    return {
        "action": action,
        "pattern_matched": state.metadata.get("_pattern_matched", "unknown"),
        # V0 (MitigationAgent.propose) devolve o diagnóstico aninhado.
        "diagnosis": dict(state.diagnosis or {}),
    }


def _safety_decision(
    state: IncidentState, validation: dict[str, Any]
) -> dict[str, Any]:
    blocked_by: list[str] = []
    if state.safety_verdict == "NEEDS_REVIEW" or validation.get("requires_human"):
        blocked_by.append("pre_validator")
    remediation = state.metadata.get("_remediation_validation") or {}
    if remediation and not remediation.get("is_safe", True):
        blocked_by.append("version_validator")
    return {
        "requires_human": bool(blocked_by) or state.safety_verdict == "NEEDS_REVIEW",
        "blocked_by": blocked_by,
    }


def terminal_outcome(state: IncidentState) -> TerminalOutcome:
    """Estado terminal honesto (B5): RESOLVED só com TNR medido ou read-only."""
    verification = state.metadata.get("_verification") or {}
    return classify_terminal(
        tnr_result=verification.get("result"),
        verification_mode=verification.get("verification_mode"),
        has_proposed_action=state.proposed_action is not None,
        auto_approved=state.safety_verdict == "APPROVED",
        needs_review=state.safety_verdict == "NEEDS_REVIEW",
        regression=state.regression_detected,
    )


def state_to_result(state: IncidentState) -> dict[str, Any]:
    """Monta o dict ``result`` (formato V0) a partir do estado do pipeline V1."""
    meta = state.metadata
    stages: dict[str, Any] = {}

    report = state.aggregated_report or {}
    detection = meta.get("_detection")
    if detection is not None:
        stages["detection"] = dict(detection)
    elif state.diagnosis is not None or report:
        stages["detection"] = {
            "is_incident": True,
            "severity": report.get("severity", "UNKNOWN"),
        }
    stages["diagnosis"] = dict(state.diagnosis or {})
    stages["mitigation"] = _mitigation_stage(state)

    validation = meta.get("_safety_validation")
    if validation is not None:
        stages["safety_validation"] = dict(validation)
    if state.safety_verdict is not None or validation is not None:
        stages["safety_decision"] = _safety_decision(state, validation or {})
    remediation = meta.get("_remediation_validation")
    if remediation is not None:
        stages["remediation_validation"] = dict(remediation)
    verification = meta.get("_verification")
    if verification is not None:
        stages["verification"] = dict(verification)

    if "_prediction" in meta:
        stages["prediction"] = meta["_prediction"]

    regression = (
        state.regression_detected or (verification or {}).get("result") == "regression"
    )
    return {
        "success": state.diagnosis is not None
        and not regression
        and terminal_outcome(state).success,
        "terminal_state": terminal_outcome(state).state,
        **({"rag_degraded": True} if meta.get("_rag_degraded") else {}),
        **(
            {"telemetry_dropped": meta["_telemetry_dropped"]}
            if meta.get("_telemetry_dropped")
            else {}
        ),
        "stages": stages,
        "timeline": list(meta.get("_timeline", [])),
        "scenario_name": meta.get("scenario", "unknown"),
        "llm_model": "heuristic" if state.no_llm else meta.get("llm_model", "unknown"),
    }
