"""Blueprint Engine — executa pipelines definidos como grafos tipados.

Orquestra a execucao de BlueprintNodes em sequencia,
aplicando regras de on_failure (ABORT, SKIP, FALLBACK).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field as dc_field
from typing import Any

from core.logging import get_logger
from domain.incident_state import IncidentState
from blueprint.nodes import (
    BlueprintNode,
    NodeResult,
    NodeType,
    OnFailure,
    PipelineDefinition,
)

log = get_logger("blueprint.engine")


class PipelineAborted(Exception):
    """Pipeline abortado por falha em node com on_failure=ABORT."""


class LLMCallLimitExceeded(Exception):
    """Limite de chamadas LLM excedido."""


@dataclass
class PipelineRunContext:
    """Contexto de execucao de um run do pipeline — rastreia chamadas LLM."""

    llm_calls: int = 0


class BlueprintEngine:
    """Engine que executa PipelineDefinitions."""

    def __init__(self, config: Any = None, store: Any = None, creds: Any = None) -> None:
        self.config = config
        self.store = store
        self.creds = creds
        self.results: list[NodeResult] = []

    def increment_llm_calls(self, ctx: PipelineRunContext, count: int = 1) -> None:
        """Incrementa contador de chamadas LLM e levanta excecao se limite excedido."""
        ctx.llm_calls += count
        max_calls = getattr(getattr(self.config, "blueprint", None), "max_llm_calls", 15)
        if ctx.llm_calls > max_calls:
            raise LLMCallLimitExceeded(
                f"LLM call limit exceeded: {ctx.llm_calls} > {max_calls}"
            )

    def run(self, pipeline: PipelineDefinition, state: IncidentState) -> IncidentState:
        """Executa o pipeline completo.

        Args:
            pipeline: Definicao do pipeline com nodes.
            state: Estado inicial do incidente.

        Returns:
            Estado final apos execucao de todos os nodes.

        Raises:
            PipelineAborted: Se um node com on_failure=ABORT falhar.
            LLMCallLimitExceeded: Se exceder limite de chamadas LLM.
        """
        log.info(
            "pipeline.started",
            pipeline=pipeline.name,
            node_count=len(pipeline.nodes),
        )

        self.results = []

        pipeline_start = time.monotonic()
        timeout_s = getattr(getattr(self.config, "blueprint", None), "pipeline_timeout_seconds", None)

        for _node_idx, node in enumerate(pipeline.nodes):
            if (
                _node_idx > 0
                and timeout_s is not None
                and (time.monotonic() - pipeline_start) > timeout_s
            ):
                raise PipelineAborted("timeout")


            start = time.monotonic()
            try:
                # Se agentic + no_llm, usa fallback diretamente
                if node.node_type == NodeType.AGENTIC and state.no_llm and node.fallback:
                    log.info("node.using_fallback", node_id=node.id, reason="no_llm")
                    state = node.fallback(state)
                    result = NodeResult(
                        node_id=node.id,
                        success=True,
                        duration_ms=(time.monotonic() - start) * 1000,
                        used_fallback=True,
                    )
                else:
                    state = node.execute(state)
                    result = NodeResult(
                        node_id=node.id,
                        success=True,
                        duration_ms=(time.monotonic() - start) * 1000,
                    )

                self.results.append(result)
                log.info(
                    "node.completed",
                    node_id=node.id,
                    duration_ms=round(result.duration_ms, 1),
                )

            except Exception as exc:
                duration = (time.monotonic() - start) * 1000
                log.error(
                    "node.failed",
                    node_id=node.id,
                    error=str(exc),
                    on_failure=node.on_failure.value,
                )

                if node.on_failure == OnFailure.ABORT:
                    self.results.append(NodeResult(
                        node_id=node.id,
                        success=False,
                        duration_ms=duration,
                        error=str(exc),
                    ))
                    raise PipelineAborted(
                        f"Node '{node.id}' failed and on_failure=ABORT: {exc}"
                    ) from exc

                elif node.on_failure == OnFailure.FALLBACK and node.fallback:
                    try:
                        state = node.fallback(state)
                        self.results.append(NodeResult(
                            node_id=node.id,
                            success=True,
                            duration_ms=(time.monotonic() - start) * 1000,
                            used_fallback=True,
                        ))
                        log.info("node.fallback_succeeded", node_id=node.id)
                    except Exception as fb_exc:
                        self.results.append(NodeResult(
                            node_id=node.id,
                            success=False,
                            duration_ms=(time.monotonic() - start) * 1000,
                            error=f"Fallback also failed: {fb_exc}",
                        ))
                        raise PipelineAborted(
                            f"Node '{node.id}' fallback also failed: {fb_exc}"
                        ) from fb_exc

                elif node.on_failure == OnFailure.SKIP:
                    self.results.append(NodeResult(
                        node_id=node.id,
                        success=False,
                        duration_ms=duration,
                        error=str(exc),
                    ))
                    log.info("node.skipped", node_id=node.id)
                    continue

        log.info(
            "pipeline.completed",
            pipeline=pipeline.name,
            nodes_run=len(self.results),
            all_success=all(r.success for r in self.results),
        )

        if self.store is not None:
            self.store.save(state.incident_id, state)

        return state
