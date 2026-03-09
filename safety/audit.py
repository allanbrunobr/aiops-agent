from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

log = structlog.get_logger()


@dataclass(frozen=True)
class SafetyAuditEvent:
    """Evento de auditoria para decisões das camadas de segurança.

    Captura contexto completo de cada decisão (pass/block) para auditoria.
    Nunca truncado ou amostrado — 100% das decisões são registradas (NFR21).
    """

    layer_name: str              # "sanitizer", "pre_validator", "tnr", "version_validator"
    correlation_id: str
    verdict: str                 # "pass", "block", "rollback", "error"
    rationale: str               # Motivo legível da decisão
    input_summary: str           # Resumo da entrada avaliada
    output_summary: str          # Resumo da saída/resultado
    triggered_rule: str | None = None   # Regra que causou block (None se pass)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


class SafetyAuditor:
    """Utilitário para emissão de eventos de auditoria de segurança.

    Garante formato consistente e 100% de cobertura nas decisões de segurança.
    Usa structlog para emissão — eventos são queryáveis por correlation_id.
    """

    @staticmethod
    def log_decision(event: SafetyAuditEvent) -> None:
        """Emite evento de auditoria via structlog.

        Formato do evento: safety.<layer_name>
        Todos os campos do SafetyAuditEvent são incluídos como contexto.
        """
        log_method = log.info if event.verdict == "pass" else log.warning

        log_method(
            f"safety.{event.layer_name}",
            correlation_id=event.correlation_id,
            verdict=event.verdict,
            rationale=event.rationale,
            input_summary=event.input_summary,
            output_summary=event.output_summary,
            triggered_rule=event.triggered_rule,
            duration_ms=event.duration_ms,
            **event.metadata,
        )

    @staticmethod
    def log_error(
        layer_name: str,
        correlation_id: str,
        error: Exception,
    ) -> None:
        """Emite evento de auditoria para erro interno de camada de segurança."""
        log.error(
            f"safety.{layer_name}",
            correlation_id=correlation_id,
            verdict="error",
            rationale=f"Internal error: {type(error).__name__}: {error}",
            input_summary="N/A",
            output_summary="N/A",
            exc_info=True,
        )
