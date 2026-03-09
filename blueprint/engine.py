"""Motor de execucao do pipeline Blueprint.

Executa nodes em ordem sequencial, gerenciando timeout global,
contador de chamadas LLM, e handlers de falha por tipo de node.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from blueprint.nodes import (
    BlueprintNode,
    NodeOutcome,
    NodeResult,
    NodeType,
    OnFailure,
    PipelineDefinition,
)
from config.loader import AppConfig
from core.logging import bind_context, get_logger
from core.models import IncidentState
from core.protocols import CredentialProvider, IncidentStore

log = get_logger("blueprint")


class PipelineAborted(Exception):
    """Pipeline abortado por falha de node ou timeout."""


class LLMCallLimitExceeded(Exception):
    """Limite de chamadas LLM excedido neste pipeline run."""


@dataclass
class PipelineRunContext:
    """Contexto mutavel de um pipeline run individual.

    Cada run cria seu proprio contexto — isolamento total (NFR12).
    """
    llm_call_count: int = 0
    node_results: list[NodeResult] = field(default_factory=list)
    start_time: float = 0.0


class BlueprintEngine:
    """Motor de execucao do pipeline Blueprint.

    Executa nodes em ordem sequencial, gerenciando timeout global,
    contador de chamadas LLM, e handlers de falha por tipo de node.
    """

    def __init__(
        self,
        config: AppConfig,
        store: IncidentStore,
        creds: CredentialProvider,
    ) -> None:
        """Inicializa o engine com dependencias injetadas.

        Args:
            config: Configuracao da aplicacao (inclui BlueprintConfig).
            store: Implementacao do IncidentStore para persistencia.
            creds: Implementacao do CredentialProvider para credenciais.
        """
        self._config = config
        self._store = store
        self._creds = creds
        self._timeout = config.blueprint.pipeline_timeout_seconds
        self._max_llm_calls = config.blueprint.max_llm_calls

    def run(
        self,
        pipeline: PipelineDefinition,
        state: IncidentState,
    ) -> IncidentState:
        """Executa pipeline completo sobre o estado do incidente.

        Args:
            pipeline: Definicao do pipeline com nodes ordenados.
            state: Estado inicial do incidente (criado externamente).

        Returns:
            Estado atualizado apos execucao de todos os nodes.

        Raises:
            PipelineAborted: Se um node com on_failure=ABORT falhar.
            LLMCallLimitExceeded: Se o limite de chamadas LLM for excedido.
        """
        ctx = PipelineRunContext(start_time=time.monotonic())

        log.info(
            "pipeline.started",
            pipeline=pipeline.name,
            correlation_id=state.correlation_id,
            nodes=len(pipeline.nodes),
            no_llm=state.no_llm,
        )

        for node in pipeline.nodes:
            # Verifica timeout global do pipeline
            elapsed = time.monotonic() - ctx.start_time
            if elapsed > self._timeout:
                log.error(
                    "pipeline.timeout",
                    pipeline=pipeline.name,
                    correlation_id=state.correlation_id,
                    elapsed_seconds=round(elapsed, 1),
                    timeout_seconds=self._timeout,
                )
                raise PipelineAborted(
                    f"Pipeline '{pipeline.name}' exceeded timeout of {self._timeout}s "
                    f"(elapsed: {elapsed:.1f}s)"
                )

            state = self._execute_node(node, state, ctx, pipeline.name)

        # Persiste estado final
        self._store.save(state.incident_id, state)

        total_ms = (time.monotonic() - ctx.start_time) * 1000
        log.info(
            "pipeline.completed",
            pipeline=pipeline.name,
            correlation_id=state.correlation_id,
            duration_ms=round(total_ms, 1),
            llm_calls=ctx.llm_call_count,
            nodes_executed=len(ctx.node_results),
        )

        return state

    def _execute_node(
        self,
        node: BlueprintNode,
        state: IncidentState,
        ctx: PipelineRunContext,
        pipeline_name: str,
    ) -> IncidentState:
        """Executa um unico node com logging, timing, e tratamento de falha."""
        node_log = bind_context(
            correlation_id=state.correlation_id,
            pipeline=pipeline_name,
            node=node.id,
        )

        node_log.info(
            "node.started",
            node_type=node.node_type.value,
            on_failure=node.on_failure.value,
        )

        start = time.monotonic()

        try:
            # Agentic node em modo --no-llm: pula direto para fallback
            if node.node_type == NodeType.AGENTIC and state.no_llm:
                if node.fallback is not None:
                    node_log.info("node.no_llm_fallback", reason="--no-llm mode active")
                    state = node.fallback(state)
                    result = NodeResult(
                        node_id=node.id,
                        outcome=NodeOutcome.FALLBACK_USED,
                        duration_ms=(time.monotonic() - start) * 1000,
                    )
                else:
                    node_log.warning("node.skipped", reason="agentic node without fallback in --no-llm mode")
                    result = NodeResult(
                        node_id=node.id,
                        outcome=NodeOutcome.SKIPPED,
                        duration_ms=(time.monotonic() - start) * 1000,
                    )
            else:
                # Execucao normal
                state = node.execute(state)
                result = NodeResult(
                    node_id=node.id,
                    outcome=NodeOutcome.SUCCESS,
                    duration_ms=(time.monotonic() - start) * 1000,
                )

        except Exception as exc:
            duration_ms = (time.monotonic() - start) * 1000
            node_log.error(
                "node.failed",
                error=str(exc),
                duration_ms=round(duration_ms, 1),
                node_type=node.node_type.value,
            )

            # Agentic node: tenta fallback antes de on_failure
            if node.node_type == NodeType.AGENTIC and node.fallback is not None:
                node_log.info("node.fallback_attempt", original_error=str(exc))
                try:
                    state = node.fallback(state)
                    result = NodeResult(
                        node_id=node.id,
                        outcome=NodeOutcome.FALLBACK_USED,
                        duration_ms=(time.monotonic() - start) * 1000,
                    )
                    node_log.info("node.fallback_success", duration_ms=round(result.duration_ms, 1))
                    ctx.node_results.append(result)
                    return state
                except Exception as fallback_exc:
                    node_log.error("node.fallback_failed", error=str(fallback_exc))

            # Aplica on_failure handler
            return self._handle_failure(node, state, ctx, exc, start, pipeline_name)

        node_log.info(
            "node.completed",
            outcome=result.outcome.value,
            duration_ms=round(result.duration_ms, 1),
        )
        ctx.node_results.append(result)
        return state

    def _handle_failure(
        self,
        node: BlueprintNode,
        state: IncidentState,
        ctx: PipelineRunContext,
        exc: Exception,
        start: float,
        pipeline_name: str,
    ) -> IncidentState:
        """Despacha handler de falha conforme on_failure do node."""
        duration_ms = (time.monotonic() - start) * 1000

        if node.on_failure == OnFailure.ABORT:
            result = NodeResult(
                node_id=node.id,
                outcome=NodeOutcome.ABORTED,
                duration_ms=duration_ms,
                error=str(exc),
            )
            ctx.node_results.append(result)
            raise PipelineAborted(
                f"Node '{node.id}' failed with on_failure=ABORT: {exc}"
            ) from exc

        elif node.on_failure == OnFailure.SKIP:
            result = NodeResult(
                node_id=node.id,
                outcome=NodeOutcome.SKIPPED,
                duration_ms=duration_ms,
                error=str(exc),
            )
            ctx.node_results.append(result)
            log.warning(
                "node.skipped_on_failure",
                node=node.id,
                pipeline=pipeline_name,
                error=str(exc),
            )
            return state

        # FALLBACK ja foi tentado acima para agentic nodes;
        # se chegou aqui, fallback nao existe ou falhou — aborta
        result = NodeResult(
            node_id=node.id,
            outcome=NodeOutcome.ABORTED,
            duration_ms=duration_ms,
            error=str(exc),
        )
        ctx.node_results.append(result)
        raise PipelineAborted(
            f"Node '{node.id}' failed (no fallback available): {exc}"
        ) from exc

    def increment_llm_calls(self, ctx: PipelineRunContext, count: int = 1) -> None:
        """Incrementa contador de chamadas LLM e verifica limite.

        Deve ser chamado por agentic nodes apos cada chamada LLM.

        Args:
            ctx: Contexto do run atual.
            count: Numero de chamadas a incrementar.

        Raises:
            LLMCallLimitExceeded: Se limite excedido.
        """
        ctx.llm_call_count += count
        if ctx.llm_call_count > self._max_llm_calls:
            log.error(
                "pipeline.llm_limit_exceeded",
                count=ctx.llm_call_count,
                limit=self._max_llm_calls,
            )
            raise LLMCallLimitExceeded(
                f"LLM call limit exceeded: {ctx.llm_call_count}/{self._max_llm_calls}"
            )
