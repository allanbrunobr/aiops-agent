"""
Mitigation Agent — Propõe remediação baseada no diagnóstico.

NUNCA executa automaticamente. Sempre requer aprovação humana.
Baseado no STRATUS: "human-in-the-loop for safety-critical actions".
"""

from __future__ import annotations

from datetime import datetime

from application.ports.reporter import (
    HUMAN_APPROVAL_REQUESTED,
    MITIGATION_PROPOSED,
    NullReporter,
    Reporter,
    TNR_VERIFIED,
)
from core.models import MitigationAction
from safety.tnr import (
    TNRSafetyGuard, TNRReport, HealthSnapshot
)

# Mapeamento de versões implantadas → versão estável anterior para rollback
ROLLBACK_VERSIONS = {
    "v3.1.0": "v3.0.2",   # api-server memory-leak
    "v4.0.0": "v3.9.2",   # data-processor unknown-fault
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
        "command": "kubectl scale deployment/{service} --replicas={current+2}",
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

        action = MitigationAction(
            description=description,
            command=pattern["command"].format(service=primary_service, current=3, prev=1),
            risk_level=pattern["risk"],
            rollback_command=pattern["rollback"].format(
                service=primary_service, current=3, prev=1
            ) if pattern.get("rollback") else None,
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

    def verify_with_tnr(self, mitigation: dict, tnr_guard: TNRSafetyGuard) -> TNRReport:
        """Verifica a mitigação usando TNR (simulado no protótipo)."""
        action = mitigation["action"]

        # Simula estados pré e pós mitigação
        pre_health = HealthSnapshot(
            timestamp=datetime.now(),
            error_rate=45.0,
            latency_p99_ms=4500.0,
            availability=55.0,
            connection_pool_active=50,
            connection_pool_max=50,
        )

        # Simula melhoria após fix
        post_health = HealthSnapshot(
            timestamp=datetime.now(),
            error_rate=0.1,
            latency_p99_ms=120.0,
            availability=99.9,
            connection_pool_active=25,
            connection_pool_max=200,
        )

        report = tnr_guard.simulate_check(action, pre_health, post_health)

        self.reporter.emit(
            TNR_VERIFIED,
            pre_snapshot=str(report.pre_snapshot),
            post_snapshot=str(report.post_snapshot),
            result=report.result.value,
            message=report.message,
        )

        return report
