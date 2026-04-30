from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import structlog

from application.ports.incident_store import IncidentStore
from domain.alert import NormalizedAlert
from domain.incident_state import IncidentState

log = structlog.get_logger()


class PipelineRunner:
    """Gerenciador de execucao concorrente de pipelines de diagnostico.

    Controla o numero maximo de pipelines em execucao simultanea
    e enfileira alertas quando o limite e atingido.
    """

    def __init__(
        self,
        max_concurrent: int = 3,
        max_queued: int = 10,
        store: IncidentStore | None = None,
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queue: asyncio.Queue[NormalizedAlert] = asyncio.Queue(maxsize=max_queued)
        self._max_concurrent = max_concurrent
        self._max_queued = max_queued
        self._store = store
        self._active_count: int = 0
        self._active_lock = asyncio.Lock()
        self._worker_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Inicia o worker de processamento da fila."""
        self._worker_task = asyncio.create_task(
            self._queue_worker(),
            name="pipeline-queue-worker",
        )
        log.info(
            "pipeline_runner.started",
            max_concurrent=self._max_concurrent,
            max_queued=self._max_queued,
        )

    async def stop(self) -> None:
        """Para o worker e aguarda pipelines em voo."""
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        log.info("pipeline_runner.stopped")

    async def submit(self, alert: NormalizedAlert) -> bool:
        """Submete alerta para processamento.

        Se ha slots disponiveis, executa imediatamente.
        Se nao, enfileira. Se a fila esta cheia, retorna False.

        Args:
            alert: Alerta normalizado para processar.

        Returns:
            True se aceito (imediato ou enfileirado), False se rejeitado.
        """
        # Tenta executar diretamente se ha slot disponivel
        if self._semaphore._value > 0:
            asyncio.create_task(
                self._run_pipeline(alert),
                name=f"pipeline-{alert.correlation_id}",
            )
            return True

        # Sem slot — tenta enfileirar
        try:
            self._queue.put_nowait(alert)
            log.info(
                "pipeline.queued",
                correlation_id=alert.correlation_id,
                queue_size=self._queue.qsize(),
            )
            return True
        except asyncio.QueueFull:
            log.warning(
                "pipeline.rejected",
                correlation_id=alert.correlation_id,
                reason="queue_full",
                max_queued=self._max_queued,
            )
            return False

    async def _run_pipeline(self, alert: NormalizedAlert) -> None:
        """Executa pipeline de diagnostico para um alerta.

        Adquire semaforo, executa pipeline isolado, libera semaforo.
        Erros sao capturados e logados — nunca propagados.
        """
        async with self._semaphore:
            async with self._active_lock:
                self._active_count += 1

            bound_log = log.bind(
                correlation_id=alert.correlation_id,
                source=alert.source,
                severity=alert.severity,
            )

            try:
                bound_log.info("pipeline.started", service=alert.service)
                await self._execute_pipeline(alert, bound_log)
                bound_log.info("pipeline.completed")
            except Exception:
                bound_log.exception("pipeline.failed")
                # Erro isolado — nao afeta outros pipelines
            finally:
                async with self._active_lock:
                    self._active_count -= 1

    async def _execute_pipeline(
        self,
        alert: NormalizedAlert,
        bound_log: Any,
    ) -> None:
        """Executa o pipeline de diagnostico propriamente dito.

        Stub V1 — sera substituido pela integracao com BlueprintEngine.
        Cria IncidentState e persiste no store.
        """
        state = IncidentState(
            incident_id=alert.correlation_id,
            correlation_id=alert.correlation_id,
            alert=alert,
            received_at=datetime.now(timezone.utc),
            started_at=alert.timestamp,
            state="DETECTING",
        )

        if self._store is not None:
            self._store.save(alert.correlation_id, state)
            bound_log.info("pipeline.state_saved", incident_id=state.incident_id)

        # Placeholder — pipeline real sera integrado quando
        # BlueprintEngine estiver conectado aos nodes de processamento
        bound_log.info("pipeline.stub_executed")

    async def _queue_worker(self) -> None:
        """Worker que drena a fila e executa pipelines quando ha slots."""
        while True:
            alert = await self._queue.get()
            try:
                await self._run_pipeline(alert)
            finally:
                self._queue.task_done()

    @property
    def active_count(self) -> int:
        """Numero de pipelines em execucao."""
        return self._active_count

    @property
    def queued_count(self) -> int:
        """Numero de alertas na fila."""
        return self._queue.qsize()
