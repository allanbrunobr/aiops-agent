from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class LifecycleEvent:
    """Evento de ciclo de vida do pipeline, consumido por ``LifecycleObserver``.

    Emitido pelas composition roots (CLI ``run_v1``, webhook
    ``PipelineRunner``, demo server) — que têm o loop async — e NÃO pelo
    ``BlueprintEngine`` (síncrono). Decisão F6-3: manter o contrato aqui e a
    emissão na borda, em vez de embutir um loop async no engine.
    """

    event_type: str  # "node.started", "node.completed", "node.failed",
    # "pipeline.started", "pipeline.completed", "pipeline.failed",
    # "prediction.warning", "prediction.alert"
    node_name: str | None  # None para eventos de pipeline
    correlation_id: str
    timestamp: datetime
    data: dict[str, Any] = field(
        default_factory=dict
    )  # Dados extras (duration_ms, error, etc.)


# Tipos de evento de lifecycle suportados (inclui eventos preditivos FR-P7)
ALLOWED_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "node.started",
        "node.completed",
        "node.failed",
        "pipeline.started",
        "pipeline.completed",
        "pipeline.failed",
        "prediction.warning",
        "prediction.alert",
        "diagnosis.completed",
        "incident.resolved",
    }
)


@runtime_checkable
class LifecycleObserver(Protocol):
    """Protocolo para observadores de eventos do pipeline."""

    async def on_event(self, event: LifecycleEvent) -> None:
        """Recebe notificacao de evento do pipeline. Nao deve lancar excecoes."""
        ...
