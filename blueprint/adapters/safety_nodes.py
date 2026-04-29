"""Adaptadores de safety nodes para o Blueprint Engine.

Encapsula os 4 safety layers (TelemetrySanitizer, VersionValidator,
PreExecutionValidator, TNR) como BlueprintNodes deterministicos
com on_failure=ABORT.

INVARIANTE: Safety layers NUNCA dependem de LLM.
INVARIANTE: Se safety falha, pipeline aborta.
"""
from __future__ import annotations

import dataclasses
from typing import Callable

from config.loader import AppConfig
from core.logging import get_logger
from domain.incident_state import IncidentState

log = get_logger("safety")


class SafetyBlockedError(Exception):
    """Acao bloqueada por um safety layer.

    Quando levantada dentro de um node com on_failure=ABORT,
    o BlueprintEngine aborta o pipeline inteiro.
    """


def make_sanitizer_node(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
    """Cria adaptador para TelemetrySanitizer (Layer 1 — pre-processing).

    Sanitiza dados de telemetria antes de qualquer processamento.
    Remove payloads adversariais e PII, mantendo abstraction map.

    INVARIANTE: Este node DEVE ser o primeiro no pipeline.
    """
    def execute(state: IncidentState) -> IncidentState:
        from pipeline.telemetry_sanitizer import TelemetrySanitizer

        sanitizer = TelemetrySanitizer()

        raw_data = state.metadata.get("raw_incident_data", {})
        logs = raw_data.get("logs", [])
        metrics = raw_data.get("metrics", [])
        traces = raw_data.get("traces", [])

        # Sanitiza cada fonte de dados
        sanitized_logs, log_report = sanitizer.sanitize_logs(logs)
        sanitized_metrics, metric_report = sanitizer.sanitize_metrics(metrics)
        sanitized_traces, trace_report = sanitizer.sanitize_traces(traces)

        log.info(
            "safety.sanitizer.completed",
            correlation_id=state.correlation_id,
            logs_sanitized=len(sanitized_logs),
            metrics_sanitized=len(sanitized_metrics),
            traces_sanitized=len(sanitized_traces),
            abstraction_map_size=len(sanitizer._abstraction_map),
            verdict="SANITIZED",
        )

        # Atualiza dados brutos com versoes sanitizadas
        updated_metadata = {
            **state.metadata,
            "raw_incident_data": {
                "logs": sanitized_logs,
                "metrics": sanitized_metrics,
                "traces": sanitized_traces,
            },
            "_sanitizer": sanitizer,  # Preserva abstraction map para pipeline
        }

        return dataclasses.replace(state, metadata=updated_metadata)

    return execute


def make_version_validator_node(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
    """Cria adaptador para VersionValidator (Layer 4 — remediation safety).

    Valida que remediacoes propostas nao contem versoes com CVEs conhecidos
    ou padroes de ataque adversarial (downgrade, PPA, config weakening).
    """
    def execute(state: IncidentState) -> IncidentState:
        from safety.version_validator import VersionValidator

        validator = VersionValidator()

        proposed_action = state.proposed_action
        if proposed_action is None:
            log.info(
                "safety.version_validator.skipped",
                correlation_id=state.correlation_id,
                reason="no_proposed_action",
                verdict="N/A",
            )
            return state

        # Valida o comando proposto
        command = proposed_action.get("command", "")
        description = proposed_action.get("description", "")

        result = validator.validate_remediation(command=command, description=description)

        log.info(
            "safety.version_validator.completed",
            correlation_id=state.correlation_id,
            risk_level=result.risk.name,
            verdict=result.risk.name,
        )

        # Se DANGEROUS ou MALICIOUS, bloqueia (raise -> on_failure=ABORT)
        if result.risk.value >= 2:  # DANGEROUS or MALICIOUS
            safety_reasons = [*state.safety_reasons, f"VersionValidator: {result.risk.name}"]
            for pattern in result.blocked_patterns:
                safety_reasons.append(f"  - {pattern}")

            log.warning(
                "safety.version_validator.blocked",
                correlation_id=state.correlation_id,
                risk_level=result.risk.name,
                blocked_patterns=result.blocked_patterns,
                verdict="BLOCKED",
            )
            raise SafetyBlockedError(
                f"VersionValidator blocked action: {result.risk.name} — {result.blocked_patterns}"
            )

        return dataclasses.replace(
            state,
            safety_reasons=[*state.safety_reasons, f"VersionValidator: {result.risk.name}"],
        )

    return execute


def make_pre_validator_node(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
    """Cria adaptador para PreExecutionValidator (Layer 2 — pre-action safety).

    Valida blast radius, blocklist, e scoring de risco antes da execucao.
    """
    def execute(state: IncidentState) -> IncidentState:
        from safety.pre_validator import PreExecutionValidator
        from safety.tnr import MitigationAction

        validator = PreExecutionValidator(
            max_blast_radius=config.safety.blast_radius_limit,
        )

        proposed_action = state.proposed_action
        if proposed_action is None:
            log.info(
                "safety.pre_validator.skipped",
                correlation_id=state.correlation_id,
                reason="no_proposed_action",
                verdict="N/A",
            )
            return state

        # Constroi MitigationAction a partir do dict
        action = MitigationAction(
            description=proposed_action.get("description", ""),
            command=proposed_action.get("command", ""),
            risk_level=proposed_action.get("risk_level", "MEDIUM"),
            rollback_command=proposed_action.get("rollback"),
        )

        diagnosis = state.diagnosis or {}

        result = validator.validate(
            action=action,
            diagnosis=diagnosis,
        )

        log.info(
            "safety.pre_validator.completed",
            correlation_id=state.correlation_id,
            risk_level=result.risk_level.value,
            approval_status=result.approval_status.value,
            verdict=result.approval_status.value,
        )

        if result.approval_status.value in ("rejected", "blocked"):
            log.warning(
                "safety.pre_validator.blocked",
                correlation_id=state.correlation_id,
                risk_level=result.risk_level.value,
                verdict="BLOCKED",
            )
            raise SafetyBlockedError(
                f"PreExecutionValidator blocked: {result.risk_level.value}"
            )

        safety_verdict = "APPROVED" if result.approval_status.value in ("auto_approved", "approved") else "NEEDS_REVIEW"

        return dataclasses.replace(
            state,
            safety_verdict=safety_verdict,
            safety_reasons=[
                *state.safety_reasons,
                f"PreValidator: {result.risk_level.value} — {result.approval_status.value}",
            ],
        )

    return execute


def make_tnr_node(config: AppConfig) -> Callable[[IncidentState], IncidentState]:
    """Cria adaptador para TNR (Layer 3 — Transactional No-Regression).

    Captura snapshot de health pre-acao, simula verificacao pos-acao.
    Se detecta regressao, bloqueia.
    """
    def execute(state: IncidentState) -> IncidentState:
        from safety.tnr import TNRSafetyGuard, HealthSnapshot, MitigationAction

        tnr = TNRSafetyGuard(
            regression_threshold=config.thresholds.regression_threshold,
        )

        proposed_action = state.proposed_action
        if proposed_action is None:
            log.info(
                "safety.tnr.skipped",
                correlation_id=state.correlation_id,
                reason="no_proposed_action",
                verdict="N/A",
            )
            return state

        # Captura snapshot pre-acao do metadata ou simula
        pre_health = state.pre_action_health
        post_health = state.post_action_health

        if pre_health is None or post_health is None:
            log.info(
                "safety.tnr.skipped",
                correlation_id=state.correlation_id,
                reason="no_health_snapshots",
                verdict="N/A",
            )
            return state

        # Converte dicts para HealthSnapshot se necessario
        if isinstance(pre_health, dict):
            pre_snapshot = HealthSnapshot(**pre_health)
        else:
            pre_snapshot = pre_health

        if isinstance(post_health, dict):
            post_snapshot = HealthSnapshot(**post_health)
        else:
            post_snapshot = post_health

        action = MitigationAction(
            description=proposed_action.get("description", ""),
            command=proposed_action.get("command", ""),
            risk_level=proposed_action.get("risk_level", "MEDIUM"),
            rollback_command=proposed_action.get("rollback"),
        )

        tnr_report = tnr.simulate_check(action, pre_snapshot, post_snapshot)

        log.info(
            "safety.tnr.completed",
            correlation_id=state.correlation_id,
            result=tnr_report.result.value,
            health_delta=tnr_report.health_delta,
            verdict=tnr_report.result.value,
        )

        if tnr_report.result.value == "regression":
            log.warning(
                "safety.tnr.regression_detected",
                correlation_id=state.correlation_id,
                health_delta=tnr_report.health_delta,
                threshold=config.thresholds.regression_threshold,
                verdict="BLOCKED",
            )
            raise SafetyBlockedError(
                f"TNR regression detected: health delta {tnr_report.health_delta:.1f} "
                f"below threshold {config.thresholds.regression_threshold}"
            )

        return dataclasses.replace(
            state,
            safety_reasons=[
                *state.safety_reasons,
                f"TNR: {tnr_report.result.value} (delta={tnr_report.health_delta:.1f})",
            ],
        )

    return execute
