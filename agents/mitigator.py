"""
Mitigation Agent — Propõe remediação baseada no diagnóstico.

NUNCA executa automaticamente. Sempre requer aprovação humana.
Baseado no STRATUS: "human-in-the-loop for safety-critical actions".
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from application.ports.reporter import (
    HUMAN_APPROVAL_REQUESTED,
    MITIGATION_PROPOSED,
    TNR_VERIFIED,
    NullReporter,
    Reporter,
)
from domain.incident import MitigationAction
from safety.tnr import (
    HealthSnapshot,
    TNRReport,
    TNRResult,
    TNRSafetyGuard,
    simulate_action_effect,
)


def health_snapshot_from_report(report) -> HealthSnapshot:
    """Deriva o snapshot PRÉ-ação da telemetria real do AggregatedReport (F2-3).

    Proxies documentados — o report não traz availability/pool diretamente:
    - error_rate: maior anomalia de métrica contendo 'error' (senão 1.0 nominal);
    - latency_p99_ms: maior total_duration_ms dos slow traces (senão 120 nominal);
    - availability: 100 - error_rate;
    - pool: anomalia contendo 'pool'/'connection' → active=max=value (exaustão);
      senão 0/100 (neutro).
    """
    anomalies = getattr(report, "metric_anomalies", []) or []
    error_values = [
        float(getattr(a, "value", 0.0))
        for a in anomalies
        if "error" in str(getattr(a, "metric_name", "")).lower()
    ]
    error_rate = max(error_values) if error_values else 1.0

    traces = getattr(report, "slow_traces", []) or []
    latency = max(
        (float(getattr(t, "total_duration_ms", 0.0)) for t in traces), default=0.0
    )
    if latency <= 0.0:
        latency = 120.0

    pool_values = [
        float(getattr(a, "value", 0.0))
        for a in anomalies
        if any(
            k in str(getattr(a, "metric_name", "")).lower()
            for k in ("pool", "connection")
        )
    ]
    if pool_values:
        pool_active = int(max(pool_values))
        pool_max = pool_active
    else:
        pool_active, pool_max = 0, 100

    return HealthSnapshot(
        timestamp=datetime.now(),
        error_rate=error_rate,
        latency_p99_ms=latency,
        availability=max(0.0, 100.0 - error_rate),
        connection_pool_active=pool_active,
        connection_pool_max=pool_max,
    )


# Mapeamento de versões implantadas → versão estável anterior para rollback
ROLLBACK_VERSIONS = {
    "v3.1.0": "v3.0.2",  # api-server memory-leak
    "v4.0.0": "v3.9.2",  # data-processor unknown-fault
}

# Padrões de remediação conhecidos
REMEDIATION_PATTERNS = {
    "connection pool": {
        "action": "Rollback config change that reduced max_connections to restore pool to 200 connections",
        "command": "kubectl rollout undo deployment/{service}",
        "risk": "LOW",
        "rollback": "kubectl rollout undo deployment/{service} --to-revision={prev}",
    },
    "cpu": {
        "action": "Scale up replicas or rollback recent deployment",
        # str.format não avalia expressões: `{current+2}` seria um NOME de
        # campo (KeyError). O valor é calculado antes, em propose().
        "command": "kubectl scale deployment/{service} --replicas={scale_up}",
        "risk": "LOW",
        "rollback": "kubectl scale deployment/{service} --replicas={current}",
    },
    "memory": {
        "action": "Rollback deployment and cleanup leaked connections to resolve memory/OOM issue",
        "command": "kubectl rollout undo deployment/{service}",
        "risk": "MEDIUM",
        "rollback": "kubectl rollout undo deployment/{service}",
    },
    "timeout": {
        "action": "Check downstream dependencies, increase timeout or fix connection issues",
        "command": "kubectl rollout undo deployment/{service}",
        "risk": "LOW",
        "rollback": "kubectl rollout undo deployment/{service}",
    },
    "unknown": {
        "action": "Rollback deployment and verify schema/data compatibility with previous version",
        "command": "kubectl rollout undo deployment/{service}",
        "risk": "LOW",
        "rollback": "kubectl rollout undo deployment/{service}",
    },
    "default": {
        "action": "Rollback to last known good deployment",
        "command": "kubectl rollout undo deployment/{service}",
        "risk": "MEDIUM",
        "rollback": "kubectl rollout undo deployment/{service}",
    },
}


# Nome de recurso Kubernetes (DNS-1123 subdomain): o nome do serviço vem da
# telemetria ou do JSON do LLM e é interpolado no comando; fora deste formato
# ele nunca entra no template.
_K8S_NAME = re.compile(
    r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$"
)
_K8S_NAME_MAX_LEN = 253


def is_valid_service_name(name: object) -> bool:
    """True se `name` pode ser interpolado num comando kubectl com segurança."""
    return (
        isinstance(name, str)
        and 0 < len(name) <= _K8S_NAME_MAX_LEN
        and _K8S_NAME.match(name) is not None
    )


class MitigationAgent:
    """Agente que propõe ações de mitigação."""

    def __init__(self, reporter: Reporter | None = None) -> None:
        self.reporter = reporter or NullReporter()

    def propose(self, diagnosis: dict) -> dict:
        """Propõe ação de mitigação baseada no diagnóstico."""
        root_cause = diagnosis.get("root_cause", "").lower()
        affected = diagnosis.get("affected_services", ["unknown"])
        primary_service = affected[0] if affected else "unknown"

        # Match com padrões conhecidos
        confidence = diagnosis.get("confidence", "medium")
        pattern = REMEDIATION_PATTERNS["default"]
        matched_pattern = "default"
        for key, p in REMEDIATION_PATTERNS.items():
            if key not in ("default", "unknown") and key in root_cause:
                pattern = p
                matched_pattern = key
                break

        # Se confiança é baixa e nenhum padrão específico matched, usar "unknown".
        if confidence == "low" and matched_pattern == "default":
            pattern = REMEDIATION_PATTERNS["unknown"]
            matched_pattern = "unknown"

        # Enriquece descrição com versão de rollback se disponível
        deployed_version = diagnosis.get("deployed_version", "")
        rollback_version = ROLLBACK_VERSIONS.get(deployed_version, "")
        description = pattern["action"].format(service=primary_service)
        if rollback_version:
            if "Rollback deployment" in description:
                description = description.replace(
                    "Rollback deployment",
                    f"Rollback deployment to {rollback_version}",
                    1,
                )
            elif rollback_version not in description:
                description += f" (rollback to {rollback_version})"

        if not is_valid_service_name(primary_service):
            # Nome fora do formato Kubernetes: não gera comando. Sem comando,
            # o PreExecutionValidator não aprova e a ação vai para humano.
            action = MitigationAction(
                description=(
                    f"Service name {str(primary_service)[:60]!r} is not a valid "
                    "Kubernetes resource name — manual investigation required, "
                    "no command generated"
                ),
                command="",
                risk_level="HIGH",
                rollback_command=None,
            )
            matched_pattern = "invalid_service"
        else:
            # Réplicas/revisão vêm do diagnóstico quando disponíveis; os defaults
            # são premissas PoC documentadas, não medições (F2-3).
            current = int(diagnosis.get("current_replicas", 3))
            prev = int(diagnosis.get("previous_revision", 1))
            fields = {
                "service": primary_service,
                "current": current,
                "scale_up": current + 2,
                "prev": prev,
            }
            action = MitigationAction(
                description=description,
                command=pattern["command"].format(**fields),
                risk_level=pattern["risk"],
                rollback_command=pattern["rollback"].format(**fields)
                if pattern.get("rollback")
                else None,
            )

        self.reporter.emit(
            MITIGATION_PROPOSED,
            description=action.description,
            command=action.command,
            risk_level=action.risk_level,
            rollback_command=action.rollback_command,
            pattern_matched=matched_pattern,
        )
        self.reporter.emit(HUMAN_APPROVAL_REQUESTED)

        return {
            "action": action,
            "pattern_matched": matched_pattern,
            "diagnosis": diagnosis,
        }

    def propose_safe(self, diagnosis: dict) -> dict:
        """propose() que nunca derruba quem chama.

        Uma falha ao montar a proposta (ex.: template quebrado, NOVO-01) vira
        uma ação de revisão manual: sem comando e com risco HIGH, que o
        PreExecutionValidator nunca auto-aprova. O erro vai para o log.
        """
        try:
            return self.propose(diagnosis)
        except Exception as exc:  # noqa: BLE001 — degrada para revisão humana, registrado
            logging.getLogger(__name__).warning(
                "Mitigation proposal failed (%r); falling back to manual review",
                exc,
            )
            action = MitigationAction(
                description=(
                    "Automatic mitigation proposal failed — manual investigation "
                    "required, no command generated"
                ),
                command="",
                risk_level="HIGH",
                rollback_command=None,
            )
            self.reporter.emit(
                MITIGATION_PROPOSED,
                description=action.description,
                command=action.command,
                risk_level=action.risk_level,
                rollback_command=None,
                pattern_matched="proposal_failed",
            )
            self.reporter.emit(HUMAN_APPROVAL_REQUESTED)
            return {
                "action": action,
                "pattern_matched": "proposal_failed",
                "diagnosis": diagnosis,
            }

    def verify_with_tnr(
        self,
        mitigation: dict,
        tnr_guard: TNRSafetyGuard,
        pre_health: HealthSnapshot | None = None,
        post_health: HealthSnapshot | None = None,
    ) -> TNRReport:
        """Verifica a mitigação com TNR a partir de snapshots de saúde REAIS.

        Sem os dois snapshots (nada executa a ação neste projeto), devolve
        NOT_VERIFIED. Antes comparava snapshots fixos que simulavam uma melhora
        e sempre dava SUCCESS (F09). Política padrão: measured-only.
        """
        action = mitigation["action"]

        if pre_health is None or post_health is None:
            tnr_report = TNRReport(
                result=TNRResult.NOT_VERIFIED,
                pre_snapshot=pre_health,
                post_snapshot=post_health,
                action=action,
                health_delta=0.0,
                message="TNR not verified: no post-action health measurement "
                "(the action was not executed)",
            )
        else:
            tnr_report = tnr_guard.simulate_check(action, pre_health, post_health)

        self.reporter.emit(
            TNR_VERIFIED,
            pre_snapshot=str(tnr_report.pre_snapshot),
            post_snapshot=str(tnr_report.post_snapshot),
            result=tnr_report.result.value,
            message=tnr_report.message,
            verification_mode=tnr_report.verification_mode,
        )

        return tnr_report

    def simulate_tnr_verification(
        self,
        mitigation: dict,
        tnr_guard: TNRSafetyGuard,
        report=None,
    ) -> TNRReport:
        """Verificação TNR SIMULADA — opt-in explícito, não é validação real (F2-3).

        O protótipo nunca executa ações em infra real (SECURITY.md):
        - PRÉ: derivado da telemetria do report quando disponível; sem report,
          baseline nominal documentado (1% erro, 120ms, 99% avail, pool 0/100);
        - PÓS: projeção determinística do efeito da ação
          (safety.tnr.simulate_action_effect), não uma medição.

        O resultado é distinguishable via TNRReport.verification_mode="simulated"
        no reporter/audit/postmortem. A política padrão é verify_with_tnr
        (measured-only → NOT_VERIFIED); este método só é usado quando o caller
        opta explicitamente por simulação.
        """
        action = mitigation["action"]

        if report is None:
            # Sem telemetria do report não há base para projetar um efeito —
            # honestidade D5: NOT_VERIFIED em vez de um sucesso inventado a
            # partir de um baseline nominal (test_reporter / F09).
            tnr_report = TNRReport(
                result=TNRResult.NOT_VERIFIED,
                pre_snapshot=None,
                post_snapshot=None,
                action=action,
                health_delta=0.0,
                message="TNR not verified: no telemetry basis for simulation "
                "(no report provided)",
            )
        else:
            pre_health = health_snapshot_from_report(report)
            post_health = simulate_action_effect(action, pre_health)
            tnr_report = tnr_guard.simulate_check(action, pre_health, post_health)

        self.reporter.emit(
            TNR_VERIFIED,
            pre_snapshot=str(tnr_report.pre_snapshot),
            post_snapshot=str(tnr_report.post_snapshot),
            result=tnr_report.result.value,
            message=tnr_report.message,
            verification_mode=tnr_report.verification_mode,
        )

        return tnr_report
