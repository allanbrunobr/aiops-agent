"""
Pre-Execution Safety Validator — Validação de segurança ANTES da execução.

Complementa o TNR (que é pós-execução) com validação pré-execução.
Inspirado em conceitos de safety para agentes autônomos:
  - STRATUS: "human-in-the-loop for safety-critical actions"
  - TRACE (Feb 2026): "governance-first execution framework"
  - AGrail: "lifelong agent guardrail"

Validações:
  1. Command Safety: Verifica se o comando proposto é seguro
  2. Blast Radius: Estima quantos serviços/recursos serão afetados
  3. Risk Scoring: Combina fatores em um score de risco
  4. Approval Rules: Define se precisa de aprovação humana

Regra principal: NUNCA executar ações destrutivas sem aprovação humana.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class RiskLevel(Enum):
    """Nível de risco da ação proposta."""
    LOW = "LOW"          # Auto-approve permitido
    MEDIUM = "MEDIUM"    # Requer confirmação
    HIGH = "HIGH"        # Requer aprovação explícita
    CRITICAL = "CRITICAL"  # Bloqueado: requer aprovação manual + justificativa


class ApprovalStatus(Enum):
    """Status da aprovação."""
    AUTO_APPROVED = "auto_approved"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    BLOCKED = "blocked"


@dataclass
class SafetyCheckResult:
    """Resultado da validação de segurança pré-execução."""
    is_safe: bool
    risk_level: RiskLevel
    approval_status: ApprovalStatus
    checks_passed: list[str]
    checks_failed: list[str]
    warnings: list[str]
    blast_radius: BlastRadius
    risk_score: float  # 0-100
    recommendation: str
    requires_human: bool

    def __str__(self):
        status = "✅ SAFE" if self.is_safe else "❌ UNSAFE"
        return (
            f"{status} | Risk: {self.risk_level.value} ({self.risk_score:.0f}/100) | "
            f"Blast radius: {self.blast_radius.affected_services} services | "
            f"Approval: {self.approval_status.value}"
        )


@dataclass
class BlastRadius:
    """Estimativa do raio de impacto da ação."""
    affected_services: int
    affected_pods_estimate: int
    is_data_destructive: bool
    is_reversible: bool
    estimated_downtime_seconds: int
    scope: str  # "single_pod", "single_service", "multi_service", "cluster_wide"


# === Regras de segurança ===

# Comandos explicitamente bloqueados (NUNCA executar)
BLOCKED_COMMANDS = [
    r"rm\s+-rf\s+/",           # rm -rf /
    r"kubectl\s+delete\s+namespace",  # delete namespace inteiro
    r"kubectl\s+delete\s+--all",      # delete tudo
    r"drop\s+database",        # drop database
    r"drop\s+table",           # drop table
    r"truncate\s+table",       # truncate
    r"format\s+",              # format disk
    r"mkfs\.",                 # format filesystem
    r"dd\s+if=.*of=/dev/",    # dd para device
    r">\s*/etc/",             # sobrescrever config do sistema
    r"chmod\s+777\s+/",       # permissões perigosas
    r"kubectl\s+exec.*--\s*sh\s+-c\s*['\"].*rm", # exec + rm dentro do pod
]

# Padrões que indicam risco elevado
HIGH_RISK_PATTERNS = [
    (r"kubectl\s+delete\s+pod", "Deleting pods causes temporary service disruption"),
    (r"kubectl\s+scale.*--replicas=0", "Scaling to 0 removes all service instances"),
    (r"kubectl\s+drain", "Draining node removes all pods from node"),
    (r"helm\s+uninstall", "Uninstalling Helm chart removes entire deployment"),
    (r"docker\s+stop", "Stopping container causes service disruption"),
    (r"systemctl\s+stop", "Stopping system service may affect dependents"),
    (r"kill\s+-9", "Force kill does not allow graceful shutdown"),
    (r"kubectl\s+cordon", "Cordoning node prevents new pod scheduling"),
]

# Padrões seguros (baixo risco)
SAFE_PATTERNS = [
    r"kubectl\s+get\s+",      # Read-only: get resources
    r"kubectl\s+describe\s+", # Read-only: describe
    r"kubectl\s+logs\s+",     # Read-only: logs
    r"kubectl\s+top\s+",      # Read-only: metrics
    r"curl\s+",               # Read-only: HTTP check
    r"ping\s+",               # Read-only: ping
    r"nslookup\s+",           # Read-only: DNS
    r"kubectl\s+rollout\s+status",  # Read-only: rollout status
]

# Padrões de rollout (risco moderado, mas reversíveis)
ROLLOUT_PATTERNS = [
    r"kubectl\s+rollout\s+undo",      # Rollback
    r"kubectl\s+rollout\s+restart",   # Restart
    r"kubectl\s+scale\s+.*--replicas=[1-9]",  # Scale up
]


class PreExecutionValidator:
    """
    Validador de segurança pré-execução.

    Complementa o TNR (pós-execução) com verificações ANTES de executar:
      - O TNR é um "airbag" — atua depois do impacto
      - O PreValidator é um "semáforo" — previne o impacto
    """

    def __init__(
        self,
        auto_approve_low_risk: bool = True,
        max_blast_radius: int = 3,  # Máximo de serviços afetados sem aprovação
        require_rollback_plan: bool = True,
    ):
        self.auto_approve_low_risk = auto_approve_low_risk
        self.max_blast_radius = max_blast_radius
        self.require_rollback_plan = require_rollback_plan
        self.history: list[SafetyCheckResult] = []

    def validate(
        self,
        action,  # MitigationAction
        diagnosis: dict,
        system_state: dict | None = None,
        *,
        correlation_id: str = "",
    ) -> SafetyCheckResult:
        """
        Valida uma ação de mitigação antes da execução.

        Args:
            action: MitigationAction proposta
            diagnosis: Resultado do diagnóstico
            system_state: Estado atual do sistema (opcional)

        Returns:
            SafetyCheckResult com decisão e detalhes
        """
        start = time.monotonic()
        checks_passed = []
        checks_failed = []
        warnings = []

        # 1. Command safety check
        cmd_safe, cmd_msg = self._check_command_safety(action.command)
        if cmd_safe:
            checks_passed.append(f"Command safety: {cmd_msg}")
        else:
            checks_failed.append(f"Command safety: {cmd_msg}")

        # 2. Blast radius estimation
        blast_radius = self._estimate_blast_radius(action, diagnosis)
        if blast_radius.affected_services <= self.max_blast_radius:
            checks_passed.append(
                f"Blast radius: {blast_radius.affected_services} services "
                f"(max: {self.max_blast_radius})"
            )
        else:
            checks_failed.append(
                f"Blast radius too large: {blast_radius.affected_services} services "
                f"exceeds max of {self.max_blast_radius}"
            )

        # 3. Data destructiveness check
        if blast_radius.is_data_destructive:
            checks_failed.append("Action is data-destructive (irreversible data loss)")
        else:
            checks_passed.append("No data destruction risk")

        # 4. Reversibility check
        if blast_radius.is_reversible:
            checks_passed.append("Action is reversible")
        else:
            warnings.append("Action may not be fully reversible")

        # 5. Rollback plan check
        if self.require_rollback_plan:
            if action.rollback_command:
                checks_passed.append(f"Rollback plan: {action.rollback_command[:50]}")
            else:
                warnings.append("No rollback command provided")

        # 6. Risk level from action metadata
        risk_map = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
        action_risk = risk_map.get(action.risk_level, 2)

        # 7. Confidence check (baixa confiança no diagnóstico = risco maior)
        confidence = diagnosis.get("confidence", "medium")
        if confidence == "low":
            warnings.append("Low diagnosis confidence increases mitigation risk")
            action_risk += 1

        # Calculate overall risk score (0-100)
        risk_score = self._calculate_risk_score(
            cmd_safe=cmd_safe,
            blast_radius=blast_radius,
            action_risk_level=action_risk,
            has_rollback=bool(action.rollback_command),
            confidence=confidence,
        )

        # Determine risk level
        risk_level = self._score_to_level(risk_score)

        # Determine approval status
        is_safe = len(checks_failed) == 0
        requires_human = True  # Default: sempre requer

        if is_safe and risk_level == RiskLevel.LOW and self.auto_approve_low_risk:
            approval_status = ApprovalStatus.AUTO_APPROVED
            requires_human = False
        elif not is_safe and any("blocked" in f.lower() or "destructive" in f.lower() for f in checks_failed):
            approval_status = ApprovalStatus.BLOCKED
            requires_human = True
        elif is_safe:
            approval_status = ApprovalStatus.PENDING_APPROVAL
        else:
            approval_status = ApprovalStatus.PENDING_APPROVAL

        # Recommendation
        recommendation = self._generate_recommendation(
            is_safe, risk_level, checks_failed, warnings, blast_radius
        )

        result = SafetyCheckResult(
            is_safe=is_safe,
            risk_level=risk_level,
            approval_status=approval_status,
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            warnings=warnings,
            blast_radius=blast_radius,
            risk_score=risk_score,
            recommendation=recommendation,
            requires_human=requires_human,
        )

        self.history.append(result)

        # Emite evento de auditoria (Story 3.4)
        try:
            from safety.audit import SafetyAuditEvent, SafetyAuditor

            duration_ms = int((time.monotonic() - start) * 1000)
            command_preview = action.command[:100] if hasattr(action, "command") else str(action)[:100]
            blocked = result.approval_status == ApprovalStatus.BLOCKED

            SafetyAuditor.log_decision(SafetyAuditEvent(
                layer_name="pre_validator",
                correlation_id=correlation_id,
                verdict="block" if blocked else "pass",
                rationale=(
                    f"Action blocked: {'; '.join(result.checks_failed)}"
                    if blocked
                    else f"Action approved. Risk score: {result.risk_score:.1f}, "
                         f"Blast radius: {result.blast_radius.affected_services} services"
                ),
                input_summary=f"Command: {command_preview}",
                output_summary=(
                    "Action rejected — not forwarded to execution"
                    if blocked
                    else "Action forwarded to TNR check"
                ),
                triggered_rule=(
                    "; ".join(result.checks_failed[:2]) if blocked else None
                ),
                duration_ms=duration_ms,
                metadata={
                    "blast_radius": result.blast_radius.affected_services,
                    "risk_score": result.risk_score,
                },
            ))
        except Exception as exc:
            try:
                from safety.audit import SafetyAuditor
                SafetyAuditor.log_error("pre_validator", correlation_id, exc)
            except Exception:
                pass

        return result

    def _check_command_safety(self, command: str) -> tuple[bool, str]:
        """Verifica se o comando é seguro."""
        cmd_lower = command.lower().strip()

        # Check blocked commands
        for pattern in BLOCKED_COMMANDS:
            if re.search(pattern, cmd_lower):
                return False, f"BLOCKED: matches dangerous pattern '{pattern}'"

        # Check high risk patterns
        for pattern, description in HIGH_RISK_PATTERNS:
            if re.search(pattern, cmd_lower):
                return True, f"HIGH RISK: {description}"

        # Check safe patterns
        for pattern in SAFE_PATTERNS:
            if re.search(pattern, cmd_lower):
                return True, "Read-only command"

        # Check rollout patterns (moderate risk but reversible)
        for pattern in ROLLOUT_PATTERNS:
            if re.search(pattern, cmd_lower):
                return True, "Rollout operation (reversible)"

        return True, "No known dangerous patterns detected"

    def _estimate_blast_radius(self, action, diagnosis: dict) -> BlastRadius:
        """Estima o raio de impacto da ação."""
        affected = diagnosis.get("affected_services", [])
        command = action.command.lower()

        # Scope estimation
        if "namespace" in command or "--all" in command:
            scope = "cluster_wide"
            pods_estimate = len(affected) * 10
        elif len(affected) > 2:
            scope = "multi_service"
            pods_estimate = len(affected) * 3
        elif "deployment" in command:
            scope = "single_service"
            pods_estimate = 3  # typical replica count
        else:
            scope = "single_pod"
            pods_estimate = 1

        # Data destructiveness
        is_destructive = any(
            kw in command
            for kw in ["delete", "drop", "truncate", "rm -rf", "format", "purge"]
        )

        # Reversibility
        is_reversible = (
            bool(action.rollback_command) or
            any(re.search(p, command) for p in ROLLOUT_PATTERNS)
        )

        # Downtime estimation
        if "rollout undo" in command:
            downtime = 30  # rollback is fast
        elif "rollout restart" in command:
            downtime = 60  # restart with rolling update
        elif "scale" in command:
            downtime = 15  # scaling is usually fast
        elif "delete" in command:
            downtime = 120  # recreation takes time
        else:
            downtime = 60

        return BlastRadius(
            affected_services=len(affected) if affected else 1,
            affected_pods_estimate=pods_estimate,
            is_data_destructive=is_destructive,
            is_reversible=is_reversible,
            estimated_downtime_seconds=downtime,
            scope=scope,
        )

    def _calculate_risk_score(
        self,
        cmd_safe: bool,
        blast_radius: BlastRadius,
        action_risk_level: int,
        has_rollback: bool,
        confidence: str,
    ) -> float:
        """Calcula score de risco de 0-100."""
        score = 0.0

        # Command safety (0-30)
        if not cmd_safe:
            score += 30
        elif action_risk_level >= 3:
            score += 20
        elif action_risk_level >= 2:
            score += 10

        # Blast radius (0-30)
        score += min(blast_radius.affected_services * 8, 30)

        # Data destructiveness (0-20)
        if blast_radius.is_data_destructive:
            score += 20

        # Reversibility (0-10)
        if not has_rollback and not blast_radius.is_reversible:
            score += 10

        # Confidence (0-10)
        confidence_scores = {"high": 0, "medium": 5, "low": 10}
        score += confidence_scores.get(confidence, 5)

        return min(score, 100)

    def _score_to_level(self, score: float) -> RiskLevel:
        """Converte score numérico em nível de risco."""
        if score >= 70:
            return RiskLevel.CRITICAL
        if score >= 45:
            return RiskLevel.HIGH
        if score >= 20:
            return RiskLevel.MEDIUM
        return RiskLevel.LOW

    def _generate_recommendation(
        self,
        is_safe: bool,
        risk_level: RiskLevel,
        failures: list[str],
        warnings: list[str],
        blast_radius: BlastRadius,
    ) -> str:
        """Gera recomendação legível."""
        if not is_safe:
            return (
                f"⛔ ACTION BLOCKED: {'; '.join(failures)}. "
                f"Review the mitigation plan and propose a safer alternative."
            )

        if risk_level == RiskLevel.CRITICAL:
            return (
                f"🔴 CRITICAL RISK: Requires senior engineer approval. "
                f"Blast radius: {blast_radius.scope} ({blast_radius.affected_services} services). "
                f"Consider: (1) canary deployment, (2) reducing scope, (3) manual execution."
            )

        if risk_level == RiskLevel.HIGH:
            return (
                f"🟠 HIGH RISK: Requires human approval before execution. "
                f"Estimated downtime: {blast_radius.estimated_downtime_seconds}s. "
                + (f"Warnings: {'; '.join(warnings)}" if warnings else "")
            )

        if risk_level == RiskLevel.MEDIUM:
            return (
                f"🟡 MEDIUM RISK: Recommended to confirm before execution. "
                f"Action is {'reversible' if blast_radius.is_reversible else 'potentially irreversible'}."
            )

        return "🟢 LOW RISK: Safe to auto-execute. Action is reversible with minimal blast radius."

    def get_summary(self) -> str:
        """Retorna resumo das validações feitas."""
        if not self.history:
            return "No validations performed."

        total = len(self.history)
        blocked = sum(1 for r in self.history if r.approval_status == ApprovalStatus.BLOCKED)
        auto = sum(1 for r in self.history if r.approval_status == ApprovalStatus.AUTO_APPROVED)
        pending = sum(1 for r in self.history if r.approval_status == ApprovalStatus.PENDING_APPROVAL)

        return (
            f"Safety validations: {total} total | "
            f"{auto} auto-approved | {pending} pending | {blocked} blocked"
        )
