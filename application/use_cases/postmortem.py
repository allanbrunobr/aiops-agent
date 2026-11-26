"""Postmortem: renderização markdown e serialização JSON do resultado.

Extraído de ``main.py`` (F5-2) e portado para o template coerente do
origin/main (veredito único para markdown E JSON, "Mitigation Proposed",
tratamento de janela saudável e diagnóstico falho). O snapshot
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


def _no_incident(result: dict[str, Any]) -> bool:
    """A detecção concluiu que não há incidente (saída antecipada para IDLE).

    Veredito, Action Items, tracking e resumo da CLI consultam este mesmo
    predicado: antes só o veredito sabia, e o resto tratava a janela saudável
    como incidente (pré-PR em 2b4f094).
    """
    return (
        result.get("stages", {}).get("detection") or {}
    ).get("is_incident") is False


def _postmortem_verdict(result: dict[str, Any]) -> dict[str, Any]:
    """Veredito único do postmortem, para o markdown E o JSON.

    Revisão pré-PR do step_postmortem inteiro (9db0afd): Summary, Timeline,
    Lições e JSON afirmavam coisas diferentes do mesmo run ("Resolved" ×
    "awaiting approval", "Mitigation applied" sem nada executado). Este
    projeto propõe a mitigação; quem executa é uma pessoa, fora dele.
    """
    stages = result.get("stages", {})
    decision = stages.get("safety_decision")
    validation = stages.get("safety_validation") or {}
    awaiting_human = bool(
        decision.get("requires_human") if decision is not None
        else validation.get("requires_human")
    )
    proposed = bool((stages.get("mitigation") or {}).get("action"))
    tnr = (stages.get("verification") or {}).get("result")
    if awaiting_human:
        resolution = "Awaiting human approval (mitigation proposed, not executed)"
    elif _no_incident(result):
        # Janela saudável: a CLI sai com exit 0; "Requires manual intervention"
        # escalaria quem automatiza em cima de meta.resolution (pré-PR 78c4689).
        resolution = "No incident detected; no action required"
    elif result.get("success") and tnr == "success":
        resolution = "Resolved"
    elif result.get("success"):
        resolution = (
            "Resolved (mitigation approved, not executed by the agent; "
            f"TNR {tnr or 'did not run'})"
        )
    else:
        resolution = "Requires manual intervention"
    return {
        "awaiting_human": awaiting_human,
        "mitigation_proposed": proposed,
        "resolution": resolution,
        "safety_blocked_by": list((decision or {}).get("blocked_by", [])),
    }


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
        # Veredito do gate junto de meta.success: o JSON não pode dizer
        # success=true sem o "awaiting_human"/"resolution" do markdown.
        **_postmortem_verdict(result),
        # Só aparece quando o RAG caiu (B1): JSON de run normal fica idêntico.
        **({"rag_degraded": True} if result.get("rag_degraded") else {}),
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
    # Sem incidente não há tarefa (mesma regra do markdown).
    action_items = [] if _no_incident(result) else get_action_items(pattern_matched)

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
    detection_stage = result.get("stages", {}).get("detection", {})

    # Um veredito só para Summary, Timeline, Lições e JSON (_postmortem_verdict).
    verdict = _postmortem_verdict(result)
    awaiting_human = verdict["awaiting_human"]
    resolution = verdict["resolution"]
    is_incident = detection_stage.get("is_incident", True)
    root_cause = diagnosis.get("root_cause") or "Unable to determine root cause"
    diagnosis_failed = any(
        t.get("to") == "FAILED" for t in result.get("timeline", [])
    )

    scenario_name = result.get("scenario_name", "payment-pool-exhaustion")
    llm_model = result.get("llm_model", "unknown")
    postmortem = f"""# Postmortem - Incident {now.strftime("%Y-%m-%d")}

## Summary

- **Date**: {now.strftime("%Y-%m-%d %H:%M")}
- **Scenario**: {scenario_name}
- **LLM Model**: {llm_model}
- **Duration**: ~{(incident.fix_time - incident.deploy_time).total_seconds() / 60:.0f} minutes (scenario: deploy to reference fix time)
- **Severity**: {result.get("stages", {}).get("detection", {}).get("severity", "UNKNOWN")}
- **Impact**: {"Service degradation detected via automated analysis" if is_incident else "No incident detected in the analysed window"}
- **Resolution**: {resolution}

## Root Cause

{root_cause}

**Confidence**: {diagnosis.get("confidence", "unknown")}

## Evidence

"""
    for i, ev in enumerate(diagnosis.get("evidence") or [], 1):
        postmortem += f"{i}. {ev}\n"

    postmortem += f"""
## Timeline

| Time | Event |
|------|-------|
| {incident.deploy_time.strftime("%H:%M:%S")} | Deployment or configuration change applied |
| {incident.spike_start.strftime("%H:%M:%S")} | {(diagnosis.get("root_cause") or "Anomaly detected")[:80]} |
| {incident.detection_time.strftime("%H:%M:%S")} | Automated detection triggered |

### State Machine Transitions

"""
    for t in result.get("timeline", []):
        postmortem += f"- **{t['time']}** {t['from']} -> {t['to']}: {t['reason']}\n"

    # O agente propõe; nada neste projeto executa a ação (agents/mitigator.py).
    postmortem += """
## Mitigation Proposed

"""
    if verdict["mitigation_proposed"]:
        postmortem += "- **Executed by the agent**: No (the agent proposes; execution is a human step)\n"
    else:
        postmortem += "- No mitigation was proposed (the pipeline stopped before mitigation)\n"
    if mitigation:
        action = mitigation.get("action")
        if action:
            postmortem += f"- **Action**: {action.description}\n"
            postmortem += f"- **Command**: `{action.command}`\n"
            postmortem += f"- **Risk Level**: {action.risk_level}\n"
            postmortem += f"- **Rollback**: `{action.rollback_command or 'N/A'}`\n"

    # Safety validation section
    safety_validation = result.get("stages", {}).get("safety_validation", {})
    safety_decision = result.get("stages", {}).get("safety_decision")
    if safety_validation:
        # Veredito combinado de todas as camadas quando disponível (F03).
        needs_human = (
            safety_decision.get("requires_human")
            if safety_decision is not None
            else safety_validation.get("requires_human")
        )
        postmortem += f"""
## Pre-Execution Safety Check

- **Risk Level**: {safety_validation.get("risk_level", "N/A")}
- **Risk Score**: {safety_validation.get("risk_score", "N/A")}/100
- **Approval Status**: {safety_validation.get("approval_status", "N/A")}
- **Requires Human**: {"Yes" if needs_human else "No"}
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
    # Sem incidente não há tarefa: a lista "default" ao lado de "no action
    # required" se contradizia.
    pattern_matched = (
        mitigation.get("pattern_matched", "default") if mitigation else "default"
    )
    items = [] if _no_incident(result) else get_action_items(pattern_matched)
    for i, item in enumerate(items, 1):
        postmortem += f"{i}. [ ] {item}\n"
    if not items:
        postmortem += "- None (no incident detected)\n"

    # Incident Tracking section (Story 6.3)
    if tracker_url and tracker_issue_key:
        browse_url = f"{tracker_url}/browse/{tracker_issue_key}"
        postmortem += f"""
## Incident Tracking

- **Jira Issue**: [{tracker_issue_key}]({browse_url})

"""

    confidence = diagnosis.get("confidence", "unknown")
    # As lições dizem o que aconteceu. Antes eram fixas e afirmavam que o TNR
    # validou a mitigação logo abaixo de "Result: not_verified" (pré-PR em
    # eee1245, a mesma contradição já corrigida no --help e nos READMEs).
    tnr_lesson = {
        "success": "TNR verification validated the mitigation outcome",
        "regression": "TNR detected a regression after the mitigation",
        "not_verified": "TNR did not verify the mitigation (no post-action health measurement)",
    }.get(verification.get("result"), "TNR verification did not run")
    if not verdict["mitigation_proposed"]:
        # Saída antecipada (sem incidente, diagnóstico FAILED): nada foi
        # mitigado, e "completed all stages" seria falso.
        pipeline_lesson = "Pipeline stopped before mitigation (see State Machine Transitions)"
    elif awaiting_human:
        pipeline_lesson = "Pipeline stopped at the human approval gate (mitigation awaiting approval)"
    else:
        pipeline_lesson = (
            "Incident response pipeline completed all stages "
            "(mitigation approved; execution is outside the agent)"
        )
    detection_lesson = (
        "Automated detection identified the incident promptly"
        if is_incident
        else "Automated detection found no incident in the analysed window"
    )
    if diagnosis_failed:
        diagnosis_lesson = "Diagnosis failed (see State Machine Transitions)"
    elif not diagnosis:
        diagnosis_lesson = "No diagnosis was run"
    elif llm_model == "heuristic":
        diagnosis_lesson = f"Heuristic diagnosis provided {confidence}-confidence assessment"
    elif llm_model == "unknown":
        diagnosis_lesson = f"Diagnosis provided {confidence}-confidence assessment"
    else:
        diagnosis_lesson = f"LLM diagnosis ({llm_model}) provided {confidence}-confidence assessment"
    postmortem += f"""
## Lessons Learned

- {detection_lesson}
- {diagnosis_lesson}
- {tnr_lesson}
- {pipeline_lesson}

---

*Generated by AIOps Agent - Agentic Incident Response System*
*Papers: STRATUS (NeurIPS 2025), OpsAgent (Nankai 2025), RCACopilot (EuroSys 2024), Acon (Microsoft 2025), AOI (Columbia 2025), TAMO (Shandong 2025), "When AIOps Become AI Oops" (RSAC Labs 2025)*
"""
    return postmortem


__all__ = [
    "ACTION_ITEMS_MAP",
    "_no_incident",
    "_postmortem_verdict",
    "get_action_items",
    "json_default",
    "serialize_health_snapshot",
    "serialize_result",
    "render_postmortem_markdown",
]
