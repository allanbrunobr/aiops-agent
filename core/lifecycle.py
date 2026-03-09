from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable


@dataclass
class LifecycleEvent:
    """Evento emitido pelo BlueprintEngine durante execução do pipeline."""

    event_type: str          # "node.started", "node.completed", "node.failed",
                             # "pipeline.started", "pipeline.completed", "pipeline.failed"
    node_name: str | None    # None para eventos de pipeline
    correlation_id: str
    timestamp: datetime
    data: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LifecycleObserver(Protocol):
    """Protocolo para observadores de eventos do pipeline."""

    async def on_event(self, event: LifecycleEvent) -> None:
        """Recebe notificação de evento do pipeline. Não deve lançar exceções."""
        ...
