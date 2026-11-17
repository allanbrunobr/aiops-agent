"""Postmortem: renderização markdown e serialização JSON do resultado.

Extraído de ``main.py`` (F5-2) **verbatim** — o snapshot
``tests/snapshots/postmortem_structure.txt`` protege cada caractere do
template. Nenhuma dependência de Rich ou de arquivo: a CLI
(``interfaces/cli/postmortem.py``) chama :func:`render_postmortem_markdown`
e :func:`serialize_result` e cuida de salvar/exibir.

O V1 (Blueprint) tem hoje um template mais pobre em
``blueprint/adapters/pipeline_nodes.py``; a convergência para este módulo
acontece em F5-4.
"""

from __future__ import annotations

import dataclasses
import enum
from datetime import datetime
from typing import Any

ACTION_ITEMS_MAP: dict[str, list[str]] = {
    "connection pool": [
        "Add connection pool size validation to CI/CD pipeline",
        "Implement config change canary deployment (gradual rollout)",
        "Add alerting for connection pool utilization > 80%",
        "Review and update runbook for connection pool incidents",
        "Add integration test for config changes affecting pool size",
    ],
    "memory": [
        "Add memory usage alerting and auto-scaling policies",
        "Investigate memory leak root cause in affected service",
        "Add heap dump collection to incident response runbook",
        "Review memory limits and resource quotas for affected services",
    ],
    "cpu": [
        "Add CPU usage alerting and auto-scaling policies",
        "Profile application for CPU hotspots under load",
        "Review resource limits and request quotas",
        "Add load testing to CI/CD pipeline",
    ],
    "timeout": [
        "Review and adjust timeout configurations across services",
        "Add circuit breaker patterns for inter-service calls",
        "Implement retry policies with exponential backoff",
        "Add timeout monitoring and alerting dashboards",
    ],
    "unknown": [
        "Investigate unidentified fault type and document findings",
        "Add monitoring for newly discovered failure pattern",
        "Escalate to engineering team for root cause analysis",
        "Create runbook entry for this incident type",
    ],
    "default": [
        "Review deployment process and add canary rollout",
        "Update incident response runbook with new pattern",
        "Add automated rollback triggers for similar incidents",
        "Implement pre-deployment validation checks",
    ],
}


def get_action_items(pattern_matched: str) -> list[str]:
    """Retorna action items com base no tipo de incidente."""
    return ACTION_ITEMS_MAP.get(pattern_matched, ACTION_ITEMS_MAP["default"])


def json_default(obj: Any) -> Any:
    """Encoder fallback para json.dumps — serializa tipos nao-nativos."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, enum.Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return dict(obj)
    return str(obj)


def serialize_health_snapshot(snapshot: Any) -> dict[str, Any]:
    """Serializa HealthSnapshot incluindo a property health_score."""
    if snapshot is None:
        return {}
    d: dict[str, Any] = dataclasses.asdict(snapshot)
    d["health_score"] = snapshot.health_score
    # datetime precisa de conversao
    if isinstance(d.get("timestamp"), datetime):
        d["timestamp"] = d["timestamp"].isoformat()
    return d


def serialize_result(result: dict[str, Any], incident: Any) -> dict[str, Any]:
    """Converte result dict completo para estrutura JSON-safe.

    Monta dict com secoes: meta, detection, diagnosis, mitigation,
    safety_validation, remediation_validation, verification,
    action_items, timeline, config.
    """
    stages = result.get("stages", {})

    # Meta
    now = datetime.now()
    meta = {
        "generated_at": now.isoformat(),
        "scenario": result.get("scenario_name", "unknown"),
        "llm_model": result.get("llm_model", "unknown"),
        "config_source": result.get("config_source", "defaults"),
        "success": result.get("success", False),
        "duration_minutes": round(
            (incident.fix_time - incident.deploy_time).total_seconds() / 60, 1
        ),
    }

    # Detection
    detection = stages.get("detection", {})

    # Diagnosis
    diagnosis = stages.get("diagnosis", {})

    # Mitigation
    mitigation_raw = stages.get("mitigation", {})
    mitigation: dict[str, Any] = {}
    if mitigation_raw:
        action = mitigation_raw.get("action")
        if action is not None:
            mitigation["action"] = (
                dataclasses.asdict(action)
                if dataclasses.is_dataclass(action) and not isinstance(action, type)
                else action
            )
        mitigation["pattern_matched"] = mitigation_raw.get("pattern_matched", "default")

    # Safety validation
    safety_validation = stages.get("safety_validation", {})

    # Remediation validation
    remediation_validation = stages.get("remediation_validation", {})

    # Verification — com snapshots que possuem health_score property
    verification_raw = stages.get("verification", {})
    verification: dict[str, Any] = {}
    if verification_raw:
        verification["result"] = verification_raw.get("result", "N/A")
        verification["health_delta"] = verification_raw.get("health_delta", "N/A")
        verification["message"] = verification_raw.get("message", "N/A")
        pre = verification_raw.get("pre_snapshot")
        post = verification_raw.get("post_snapshot")
        if pre is not None:
            verification["pre_snapshot"] = serialize_health_snapshot(pre)
        if post is not None:
            verification["post_snapshot"] = serialize_health_snapshot(post)

    # Action items
    pattern_matched = (
        mitigation_raw.get("pattern_matched", "default")
        if mitigation_raw
        else "default"
    )
    action_items = get_action_items(pattern_matched)

    # Timeline
    timeline = {
        "incident_timeline": {
            "deploy_time": incident.deploy_time.isoformat(),
            "spike_start": incident.spike_start.isoformat(),
            "detection_time": incident.detection_time.isoformat(),
            "fix_time": incident.fix_time.isoformat(),
        },
        "state_transitions": result.get("timeline", []),
    }

    # Config
    config: dict[str, Any] = {}
    tc = result.get("threshold_config")
    if tc is not None:
        config["thresholds"] = (
            tc.thresholds.model_dump()
            if hasattr(tc, "thresholds")
            else dataclasses.asdict(tc)
        )
    config["source"] = result.get("config_source", "defaults")

    # Tracking (Story 6.3)
    tracking = stages.get("tracking", {})

    serialized: dict[str, Any] = {
        "meta": meta,
        "detection": detection,
        "diagnosis": diagnosis,
        "mitigation": mitigation,
        "safety_validation": safety_validation,
        "remediation_validation": remediation_validation,
        "verification": verification,
        "action_items": action_items,
        "timeline": timeline,
        "config": config,
    }
    if tracking:
        serialized["tracking"] = tracking

    return serialized


def render_postmortem_markdown(
    result: dict[str, Any],
    incident: Any,
    *,
    now: datetime,
    tracker_url: str | None = None,
    tracker_issue_key: str | None = None,
) -> str:
    """Renderiza o postmortem em markdown (template da CLI, Story 5).

    ``now`` é injetado para que quem salva o arquivo use o MESMO instante no
    nome e no conteúdo (comportamento original de ``step_postmortem``).
    """
    diagnosis = result.get("stages", {}).get("diagnosis", {})
    mitigation = result.get("stages", {}).get("mitigation", {})
    verification = result.get("stages", {}).get("verification", {})

    scenario_name = result.get("scenario_name", "payment-pool-exhaustion")
    llm_model = result.get("llm_model", "unknown")
    postmortem = f"""# Postmortem - Incident {now.strftime("%Y-%m-%d")}

## Summary

- **Date**: {now.strftime("%Y-%m-%d %H:%M")}
- **Scenario**: {scenario_name}
- **LLM Model**: {llm_model}
- **Duration**: ~{(incident.fix_time - incident.deploy_time).total_seconds() / 60:.0f} minutes
- **Severity**: {result.get("stages", {}).get("detection", {}).get("severity", "UNKNOWN")}
- **Impact**: Service degradation detected via automated analysis
- **Resolution**: {"Resolved" if result.get("success") else "Requires manual intervention"}

## Root Cause

{diagnosis.get("root_cause", "Unable to determine root cause")}

**Confidence**: {diagnosis.get("confidence", "unknown")}

## Evidence

"""
    for i, ev in enumerate(diagnosis.get("evidence", []), 1):
        postmortem += f"{i}. {ev}\n"

    postmortem += f"""
## Timeline

| Time | Event |
|------|-------|
| {incident.deploy_time.strftime("%H:%M:%S")} | Deployment or configuration change applied |
| {incident.spike_start.strftime("%H:%M:%S")} | {diagnosis.get("root_cause", "Anomaly detected")[:80]} |
| {incident.detection_time.strftime("%H:%M:%S")} | Automated detection triggered |
| {incident.fix_time.strftime("%H:%M:%S")} | Mitigation applied |

### State Machine Transitions

"""
    for t in result.get("timeline", []):
        postmortem += f"- **{t['time']}** {t['from']} -> {t['to']}: {t['reason']}\n"

    postmortem += """
## Mitigation Applied

"""
    if mitigation:
        action = mitigation.get("action")
        if action:
            postmortem += f"- **Action**: {action.description}\n"
            postmortem += f"- **Command**: `{action.command}`\n"
            postmortem += f"- **Risk Level**: {action.risk_level}\n"
            postmortem += f"- **Rollback**: `{action.rollback_command or 'N/A'}`\n"

    # Safety validation section
    safety_validation = result.get("stages", {}).get("safety_validation", {})
    if safety_validation:
        postmortem += f"""
## Pre-Execution Safety Check

- **Risk Level**: {safety_validation.get("risk_level", "N/A")}
- **Risk Score**: {safety_validation.get("risk_score", "N/A")}/100
- **Approval Status**: {safety_validation.get("approval_status", "N/A")}
- **Requires Human**: {"Yes" if safety_validation.get("requires_human") else "No"}
- **Blast Radius**: {safety_validation.get("blast_radius_scope", "N/A")}
- **Recommendation**: {safety_validation.get("recommendation", "N/A")}
"""

    # Remediation validation section
    remediation_validation = result.get("stages", {}).get("remediation_validation", {})
    if remediation_validation:
        postmortem += f"""
## Remediation CVE/Version Validation

- **Risk**: {remediation_validation.get("risk", "N/A")}
- **Is Safe**: {"Yes" if remediation_validation.get("is_safe") else "No"}
- **Blocked Patterns**: {", ".join(remediation_validation.get("blocked_patterns", [])) or "None"}
- **Warnings**: {", ".join(remediation_validation.get("warnings", [])) or "None"}
- **Recommendation**: {remediation_validation.get("recommendation", "N/A")}
"""

    postmortem += f"""
## TNR Verification

- **Result**: {verification.get("result", "N/A")}
- **Health Delta**: {verification.get("health_delta", "N/A")}
- **Message**: {verification.get("message", "N/A")}

## Action Items

"""

    # Gera action items dinâmicos com base no pattern_matched da mitigação.
    pattern_matched = (
        mitigation.get("pattern_matched", "default") if mitigation else "default"
    )
    items = get_action_items(pattern_matched)
    for i, item in enumerate(items, 1):
        postmortem += f"{i}. [ ] {item}\n"

    # Incident Tracking section (Story 6.3)
    if tracker_url and tracker_issue_key:
        browse_url = f"{tracker_url}/browse/{tracker_issue_key}"
        postmortem += f"""
## Incident Tracking

- **Jira Issue**: [{tracker_issue_key}]({browse_url})

"""

    confidence = diagnosis.get("confidence", "unknown")
    postmortem += f"""
## Lessons Learned

- Automated detection identified the incident promptly
- Heuristic diagnosis provided {confidence}-confidence assessment
- TNR verification validated the mitigation outcome
- Incident response pipeline completed all stages successfully

---

*Generated by AIOps Agent - Agentic Incident Response System*
*Papers: STRATUS (NeurIPS 2025), OpsAgent (Nankai 2025), RCACopilot (EuroSys 2024), Acon (Microsoft 2025), AOI (Columbia 2025), TAMO (Shandong 2025), "When AIOps Become AI Oops" (RSAC Labs 2025)*
"""
    return postmortem


__all__ = [
    "ACTION_ITEMS_MAP",
    "get_action_items",
    "json_default",
    "serialize_health_snapshot",
    "serialize_result",
    "render_postmortem_markdown",
]
