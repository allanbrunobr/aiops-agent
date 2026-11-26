"""Adapter IncidentState (V1) -> dict ``result`` no formato do V0.

O renderer rico (``application.use_cases.postmortem``) consome o dict que o
engine V0 montava. Este módulo reconstrói esse dict a partir do estado e dos
dados estruturados que os nodes do blueprint guardam em ``state.metadata``:

- ``_timeline``: transições da máquina de estados
- ``_pattern_matched``: padrão da mitigação (alimenta Action Items)
- ``_safety_validation`` / ``_remediation_validation``: dicts dos validators
- ``_verification``: resultado do TNR

Função pura, sem I/O. Seções sem captura saem vazias — o renderer já as trata.
"""

from __future__ import annotations

from typing import Any

from domain.incident_state import IncidentState


def _mitigation_stage(state: IncidentState) -> dict[str, Any]:
    proposed = state.proposed_action
    if not proposed:
        return {}
    # Import tardio: safety.tnr é camada de domínio de safety, sem custo no
    # import deste módulo para quem só lê o resultado.
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


def state_to_result(state: IncidentState) -> dict[str, Any]:
    """Monta o dict ``result`` (formato V0) a partir do estado do pipeline V1."""
    meta = state.metadata
    stages: dict[str, Any] = {}

    report = state.aggregated_report or {}
    if state.diagnosis is not None or report:
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

    regression = (
        state.regression_detected or (verification or {}).get("result") == "regression"
    )
    return {
        "success": state.diagnosis is not None and not regression,
        "stages": stages,
        "timeline": list(meta.get("_timeline", [])),
        "scenario_name": meta.get("scenario", "unknown"),
        "llm_model": "heuristic" if state.no_llm else meta.get("llm_model", "unknown"),
    }
