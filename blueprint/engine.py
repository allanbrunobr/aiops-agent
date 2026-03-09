from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import structlog

from core.lifecycle import LifecycleEvent, LifecycleObserver

log = structlog.get_logger()


class BlueprintNode:
    """Interface base para nos do pipeline.

    Cada no recebe um estado e retorna o estado atualizado.
    """

    def __init__(self, node_id: str) -> None:
        self.id = node_id

    async def execute(self, state: dict[str, Any]) -> dict[str, Any]:
        """Executa o no e retorna o estado atualizado."""
        raise NotImplementedError


class BlueprintEngine:
    """Motor de execucao do pipeline baseado no padrao STRATUS.

    Gerencia a execucao sequencial de nos e emite eventos de ciclo de vida
    para observadores registrados.
    """

    def __init__(self) -> None:
        self._nodes: list[BlueprintNode] = []
        self._observers: list[LifecycleObserver] = []

    def add_node(self, node: BlueprintNode) -> None:
        """Adiciona no ao pipeline."""
        self._nodes.append(node)

    def add_observer(self, observer: LifecycleObserver) -> None:
        """Registra observador de eventos do ciclo de vida do pipeline."""
        self._observers.append(observer)

    async def _emit_event(self, event: LifecycleEvent) -> None:
        """Notifica todos os observadores. Falhas nos observers sao logadas, nunca propagadas."""
        for observer in self._observers:
            try:
                await observer.on_event(event)
            except Exception:
                log.warning(
                    "observer.failed",
                    observer=type(observer).__name__,
                    event_type=event.event_type,
                    exc_info=True,
                )

    async def _run_node(self, node: BlueprintNode, state: dict[str, Any]) -> dict[str, Any]:
        """Executa um no do pipeline com emissao de eventos."""
        correlation_id = state.get("correlation_id", "unknown")

        await self._emit_event(LifecycleEvent(
            event_type="node.started",
            node_name=node.id,
            correlation_id=correlation_id,
            timestamp=datetime.utcnow(),
        ))

        start = time.monotonic()
        try:
            state = await node.execute(state)
            duration_ms = int((time.monotonic() - start) * 1000)
            await self._emit_event(LifecycleEvent(
                event_type="node.completed",
                node_name=node.id,
                correlation_id=correlation_id,
                timestamp=datetime.utcnow(),
                data={"duration_ms": duration_ms},
            ))
        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            await self._emit_event(LifecycleEvent(
                event_type="node.failed",
                node_name=node.id,
                correlation_id=correlation_id,
                timestamp=datetime.utcnow(),
                data={"duration_ms": duration_ms, "error": str(exc)},
            ))
            raise
        return state

    async def run(self, initial_state: dict[str, Any]) -> dict[str, Any]:
        """Executa o pipeline completo com emissao de eventos de ciclo de vida."""
        state = initial_state
        correlation_id = state.get("correlation_id", "unknown")

        await self._emit_event(LifecycleEvent(
            event_type="pipeline.started",
            node_name=None,
            correlation_id=correlation_id,
            timestamp=datetime.utcnow(),
            data={k: v for k, v in state.items() if k != "correlation_id"},
        ))

        start = time.monotonic()
        try:
            for node in self._nodes:
                state = await self._run_node(node, state)

            total_duration_ms = int((time.monotonic() - start) * 1000)
            await self._emit_event(LifecycleEvent(
                event_type="pipeline.completed",
                node_name=None,
                correlation_id=correlation_id,
                timestamp=datetime.utcnow(),
                data={**state, "total_duration_ms": total_duration_ms},
            ))
        except Exception as exc:
            total_duration_ms = int((time.monotonic() - start) * 1000)
            await self._emit_event(LifecycleEvent(
                event_type="pipeline.failed",
                node_name=None,
                correlation_id=correlation_id,
                timestamp=datetime.utcnow(),
                data={"error": str(exc), "total_duration_ms": total_duration_ms},
            ))
            raise

        return state
