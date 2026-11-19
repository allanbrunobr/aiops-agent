from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import structlog

from application.ports.incident_store import IncidentStore
from core.lifecycle import LifecycleEvent
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
        engine: Any = None,
        config: Any = None,
        notifier: Any = None,
    ) -> None:
        self._slots_free = max_concurrent
        self._queue: asyncio.Queue[NormalizedAlert] = asyncio.Queue(maxsize=max_queued)
        self._max_concurrent = max_concurrent
        self._max_queued = max_queued
        self._store = store
        self._engine = engine
        self._config = config
        self._notifier = notifier
        self._active_count: int = 0
        self._active_lock = asyncio.Lock()
        self._admit_lock = asyncio.Lock()
        self._worker_task: asyncio.Task[None] | None = None
        # Referência forte às tasks de pipeline: sem ela o GC pode coletar uma
        # task em execução, e stop() não tem o que aguardar (4B-10).
        self._tasks: set[asyncio.Task[None]] = set()
        self._closing = False

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

    async def stop(self, drain_timeout: float = 30.0) -> None:
        """Fecha a admissão e drena pipelines em voo e enfileirados.

        Alertas já aceitos (202) rodam até o fim, dentro de `drain_timeout`
        segundos; o que sobrar é cancelado e registrado. Antes, stop()
        cancelava só o worker ocioso e retornava, perdendo os aceitos.
        """
        self._closing = True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + drain_timeout
        while self._tasks:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.wait(set(self._tasks), timeout=remaining)
        leftover = set(self._tasks)
        for task in leftover:
            task.cancel()
        if leftover:
            await asyncio.gather(*leftover, return_exceptions=True)
        dropped_queued = self._queue.qsize()
        if leftover or dropped_queued:
            log.warning(
                "pipeline_runner.drain_incomplete",
                cancelled=len(leftover),
                dropped_queued=dropped_queued,
            )
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
        log.info("pipeline_runner.stopped")

    def _spawn(self, alert: NormalizedAlert) -> None:
        task = asyncio.create_task(
            self._run_pipeline(alert),
            name=f"pipeline-{alert.correlation_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def submit(self, alert: NormalizedAlert) -> bool:
        """Submete alerta para processamento.

        Se ha slots disponiveis, executa imediatamente.
        Se nao, enfileira. Se a fila esta cheia, retorna False.

        Args:
            alert: Alerta normalizado para processar.

        Returns:
            True se aceito (imediato ou enfileirado), False se rejeitado.
        """
        # O slot é um contador sob o lock. wait_for(acquire(), 0) cedia
        # o event loop e o worker esvaziava a fila antes do próximo submit.
        async with self._admit_lock:
            if self._closing:
                # Encerrando: recusa (o router responde 503 e o emissor reenvia).
                log.warning(
                    "pipeline.rejected",
                    correlation_id=alert.correlation_id,
                    reason="shutting_down",
                )
                return False
            if self._slots_free > 0:
                self._slots_free -= 1
                self._spawn(alert)
                return True

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

    async def _release_slot_or_continue(self) -> None:
        """Devolve o slot, ou entrega o mesmo slot ao próximo alerta da fila."""
        async with self._admit_lock:
            try:
                nxt = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                self._slots_free += 1
                return
            self._queue.task_done()
            self._spawn(nxt)

    async def _run_pipeline(self, alert: NormalizedAlert) -> None:
        """Executa pipeline de diagnostico para um alerta.

        O slot já foi reservado em submit() ou em _release_slot_or_continue().
        Erros sao capturados e logados — nunca propagados.
        """
        try:
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
            finally:
                async with self._active_lock:
                    self._active_count -= 1
        finally:
            await self._release_slot_or_continue()

    async def _execute_pipeline(
        self,
        alert: NormalizedAlert,
        bound_log: Any,
    ) -> None:
        """Executa o pipeline V1 real para o alerta (F3-3).

        O webhook traz metadados do alerta, não séries de telemetria — o PoC
        usa o sample simulado como fonte (sinalizado no estado e nos logs).
        O engine roda em thread para não bloquear o event loop. Ao final:
        estado persistido, postmortem gerado e notificação Slack (se wired).
        """
        from app.builders import build_pipeline
        from data.sample_incident import create_incident

        incident = create_incident()
        state = IncidentState(
            incident_id=alert.correlation_id,
            correlation_id=alert.correlation_id,
            alert=alert,
            received_at=datetime.now(timezone.utc),
            started_at=alert.timestamp,
            state="DETECTING",
            no_llm=True,
            no_rag=True,
            metadata={
                "raw_incident_data": {
                    "logs": incident.logs,
                    "metrics": incident.metrics,
                    "traces": incident.traces,
                },
                "telemetry_source": "simulated-sample",
            },
        )

        if self._store is not None:
            self._store.save(alert.correlation_id, state)

        if self._engine is None:
            bound_log.warning(
                "pipeline.engine_unavailable",
                detail="runner sem engine — estado inicial salvo, diagnóstico não executado",
            )
            return

        cid = alert.correlation_id
        if self._notifier is not None:
            await self._notifier.on_event(
                LifecycleEvent(
                    event_type="pipeline.started",
                    node_name=None,
                    correlation_id=cid,
                    timestamp=datetime.now(timezone.utc),
                    data={"source": alert.source, "service": alert.service},
                )
            )

        pipeline = build_pipeline(self._config)
        t0 = time.monotonic()
        try:
            final = await asyncio.to_thread(self._engine.run, pipeline, state)
        except Exception as exc:
            if self._notifier is not None:
                await self._notifier.on_event(
                    LifecycleEvent(
                        event_type="pipeline.failed",
                        node_name=None,
                        correlation_id=cid,
                        timestamp=datetime.now(timezone.utc),
                        data={"error": str(exc)},
                    )
                )
            raise
        duration_ms = (time.monotonic() - t0) * 1000

        if self._store is not None:
            self._store.save(cid, final)
            bound_log.info("pipeline.state_saved", incident_id=final.incident_id)

        diagnosis = final.diagnosis or {}
        bound_log.info(
            "pipeline.executed",
            root_cause=diagnosis.get("root_cause"),
            safety_verdict=final.safety_verdict,
            telemetry_source="simulated-sample",
            duration_ms=round(duration_ms, 1),
        )

        if self._notifier is not None:
            await self._notifier.on_event(
                LifecycleEvent(
                    event_type="pipeline.completed",
                    node_name=None,
                    correlation_id=cid,
                    timestamp=datetime.now(timezone.utc),
                    data={
                        "diagnosis": diagnosis,
                        "root_cause": diagnosis.get("root_cause", ""),
                        "postmortem": final.postmortem_md or "",
                        "safety_verdict": final.safety_verdict,
                        "service": alert.service,
                        "duration_ms": round(duration_ms, 1),
                    },
                )
            )

    async def _queue_worker(self) -> None:
        """Task cancelável de start/stop.

        A fila não é lida aqui. Quem lê é _release_slot_or_continue, no
        mesmo lock da admissão, para o teto continuar sendo
        max_concurrent + max_queued.
        """
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise

    @property
    def active_count(self) -> int:
        """Numero de pipelines em execucao."""
        return self._active_count

    @property
    def queued_count(self) -> int:
        """Numero de alertas na fila."""
        return self._queue.qsize()
