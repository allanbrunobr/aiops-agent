"""Orquestrador de segurança centralizado do AIOps Agent.

Encapsula as 4 camadas de validação de segurança em ordem:
  Sanitizer → PreValidator → TNR → VersionValidator

Permite que CLI (main.py) e MCP (mcp_server/) usem o mesmo ponto de entrada
sem duplicação de lógica.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from domain.incident import MitigationAction
    from safety.pre_validator import PreExecutionValidator
    from safety.tnr import TNRSafetyGuard
    from safety.version_validator import VersionValidator


class _TextSanitizer(Protocol):
    """Contrato mínimo do sanitizer injetado (Camada 1)."""

    def sanitize_text(self, text: str) -> str: ...


@dataclass
class SafetyValidationResult:
    """Resultado agregado das 4 camadas de validação de segurança."""

    approved: bool
    """True se TODAS as camadas aprovaram a ação."""

    risk_score: float
    """Score de risco de 0-100 (maior = mais perigoso)."""

    blocked_by: list[str] = field(default_factory=list)
    """Nomes das camadas que bloquearam a ação."""

    layer_details: dict[str, object] = field(default_factory=dict)
    """Detalhes de cada camada: pre_validator, tnr, version_validator."""

    rationale: str = ""
    """Justificativa humano-legível do veredicto."""


class SafetyOrchestrator:
    """Orquestrador que executa as 4 camadas de segurança em ordem.

    Aceita injeção de dependência para testabilidade com mocks.

    Exemplo de uso:
        orchestrator = SafetyOrchestrator(
            pre_validator=PreExecutionValidator(),
            tnr=TNRSafetyGuard(),
            version_validator=VersionValidator(),
        )
        result = orchestrator.validate(action, diagnosis)
    """

    def __init__(
        self,
        pre_validator: PreExecutionValidator | None = None,
        tnr: TNRSafetyGuard | None = None,
        version_validator: VersionValidator | None = None,
        sanitizer: _TextSanitizer | None = None,
    ) -> None:
        """Inicializa o orquestrador com validadores injetados.

        Args:
            pre_validator: Camada 2 — blast radius e blocklist de comandos.
            tnr:           Camada 3 — verificação de regressão de saúde.
            version_validator: Camada 4 — CVE e versões.
            sanitizer:     Camada 1 — sanitização de PII/injeção (opcional).
        """
        self._sanitizer = sanitizer

        if pre_validator is None:
            from safety.pre_validator import PreExecutionValidator

            self._pre_validator: PreExecutionValidator = PreExecutionValidator()
        else:
            self._pre_validator = pre_validator

        if tnr is None:
            from safety.tnr import TNRSafetyGuard

            self._tnr: TNRSafetyGuard = TNRSafetyGuard()
        else:
            self._tnr = tnr

        if version_validator is None:
            from safety.version_validator import VersionValidator

            self._version_validator: VersionValidator = VersionValidator()
        else:
            self._version_validator = version_validator

    def validate(
        self,
        action: MitigationAction,
        diagnosis: dict | None = None,
        system_state: dict | None = None,
        *,
        correlation_id: str = "",
    ) -> SafetyValidationResult:
        """Executa todas as camadas de segurança em ordem, sem curto-circuito.

        Roda TODAS as camadas mesmo quando uma falha, para coletar evidências
        completas. A arquitetura de 4 camadas nunca é pulada.

        Args:
            action:       MitigationAction a validar.
            diagnosis:    Resultado do diagnóstico (dict) — usado pelo PreValidator.
            system_state: Estado do sistema — contexto adicional (opcional).
            correlation_id: ID de correlação para rastreamento de auditoria.

        Returns:
            SafetyValidationResult com veredicto agregado.
        """
        diag = diagnosis or {}
        blocked_by: list[str] = []
        layer_details: dict[str, object] = {}
        risk_scores: list[float] = []

        # Camada 1: Sanitizer (se presente). Comando que o sanitizer altera
        # (PII/payload) bloqueia; exceção falha FECHADO (antes contava aprovado).
        if self._sanitizer is not None:
            command = getattr(action, "command", "")
            try:
                sanitized = self._sanitizer.sanitize_text(command)
                sanitizer_ok = sanitized == command
                layer_details["sanitizer"] = {"passed": sanitizer_ok}
            except Exception as exc:  # noqa: BLE001 — falha fechado, registrado no detalhe
                sanitizer_ok = False
                layer_details["sanitizer"] = {"passed": False, "error": repr(exc)}
            if not sanitizer_ok:
                blocked_by.append("sanitizer")

        # Camada 2: PreExecutionValidator — blast radius + blocklist
        try:
            pre_result = self._pre_validator.validate(
                action,
                diag,
                system_state,
                correlation_id=correlation_id,
            )
        except TypeError:
            # Fallback para validators que não aceitam correlation_id
            pre_result = self._pre_validator.validate(action, diag)
        layer_details["pre_validator"] = pre_result
        risk_scores.append(float(pre_result.risk_score))
        if not pre_result.is_safe:
            blocked_by.append("pre_validator")

        # Camada 3: TNR (D-TNR — reconcilia F2-3 simulação + D5 honestidade).
        # Com snapshots pré/pós MEDIDOS em system_state, compara saúde real.
        # Sem medição pós-ação, projeta o efeito da ação (simulate_action_effect):
        # uma projeção de REGRESSÃO (ação destrutiva que escapa do pre_validator,
        # ex.: scale-to-zero) BLOQUEIA; uma projeção benigna é honestamente
        # NOT_VERIFIED (não há medição real) e NÃO bloqueia. Exceção falha
        # FECHADO. verification_mode="simulated" deixa explícito que é projeção.
        from safety.tnr import (
            HealthSnapshot,
            TNRReport,
            TNRResult,
            simulate_action_effect,
        )

        state = system_state or {}
        pre_health = state.get("pre_health")
        post_health = state.get("post_health")
        try:
            if pre_health is None:
                pre_health = HealthSnapshot(
                    timestamp=datetime.now(),
                    error_rate=1.0,
                    latency_p99_ms=120.0,
                    availability=99.0,
                    connection_pool_active=0,
                    connection_pool_max=100,
                )
            elif isinstance(pre_health, dict):
                pre_health = HealthSnapshot(**pre_health)
            measured = post_health is not None
            if isinstance(post_health, dict):
                post_health = HealthSnapshot(**post_health)
            if post_health is None:
                post_health = simulate_action_effect(action, pre_health)
            try:
                tnr_report = self._tnr.simulate_check(
                    action, pre_health, post_health, correlation_id=correlation_id
                )
            except TypeError:
                tnr_report = self._tnr.simulate_check(action, pre_health, post_health)
            if not measured and tnr_report.result == TNRResult.SUCCESS:
                # Projeção benigna sem medição real => NOT_VERIFIED (D5), mas
                # preserva os snapshots distintos para auditoria (F2-3).
                tnr_report = TNRReport(
                    result=TNRResult.NOT_VERIFIED,
                    pre_snapshot=pre_health,
                    post_snapshot=post_health,
                    action=action,
                    health_delta=tnr_report.health_delta,
                    message="TNR not verified: no post-action health measurement",
                    verification_mode="simulated",
                )
            layer_details["tnr"] = tnr_report
            if tnr_report.result != TNRResult.SUCCESS:
                # REGRESSÃO (medida ou projetada) bloqueia; NOT_VERIFIED sem
                # medição NÃO bloqueia (não há evidência de dano).
                if measured or tnr_report.result != TNRResult.NOT_VERIFIED:
                    blocked_by.append("tnr")
        except Exception as exc:  # noqa: BLE001 — falha fechado, registrado no detalhe
            layer_details["tnr"] = {
                "verified": False,
                "passed": False,
                "error": repr(exc),
            }
            blocked_by.append("tnr")

        # Camada 4: VersionValidator — CVE e versões
        # Tenta validate_mitigation_action primeiro (interface completa), depois validate_remediation
        if hasattr(self._version_validator, "validate_mitigation_action"):
            version_result = self._version_validator.validate_mitigation_action(action)
        else:
            command = getattr(action, "command", str(action))
            version_result = self._version_validator.validate_remediation(command)  # type: ignore[union-attr]
        layer_details["version_validator"] = version_result
        if not version_result.is_safe:
            blocked_by.append("version_validator")
            try:
                risk_scores.append(float(version_result.risk.value) * 25)
            except (AttributeError, TypeError):
                risk_scores.append(50.0)

        # Agrega resultado
        approved = len(blocked_by) == 0
        avg_risk = sum(risk_scores) / len(risk_scores) if risk_scores else 0.0

        if approved:
            rationale = "Action approved by all safety layers."
        else:
            rationale = f"Action blocked by: {', '.join(blocked_by)}."

        return SafetyValidationResult(
            approved=approved,
            risk_score=round(avg_risk, 1),
            blocked_by=blocked_by,
            layer_details=layer_details,
            rationale=rationale,
        )


def demo_safety_orchestrator() -> None:
    """Demonstração do SafetyOrchestrator com cenários safe e unsafe."""
    from domain.incident import MitigationAction

    orchestrator = SafetyOrchestrator()

    # Cenário seguro
    safe_action = MitigationAction(
        description="Restart service pod",
        command="kubectl rollout restart deployment/api-server",
        risk_level="LOW",
        rollback_command="kubectl rollout undo deployment/api-server",
    )
    result = orchestrator.validate(safe_action, diagnosis={"confidence": "high"})
    print(f"Safe action approved: {result.approved}, risk: {result.risk_score}")

    # Cenário inseguro
    unsafe_action = MitigationAction(
        description="Drop all tables",
        command="DROP TABLE *; rm -rf /",
        risk_level="HIGH",
    )
    result = orchestrator.validate(unsafe_action, diagnosis={"confidence": "low"})
    print(f"Unsafe action approved: {result.approved}, blocked_by: {result.blocked_by}")
