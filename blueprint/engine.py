"""Blueprint Engine — executa pipelines definidos como grafos tipados.

Orquestra a execucao de BlueprintNodes em sequencia,
aplicando regras de on_failure (ABORT, SKIP, FALLBACK).
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any

from adapters.llm.counting_client import LLMBudgetExceeded
from blueprint.nodes import (
    NodeResult,
    NodeType,
    OnFailure,
    PipelineDefinition,
)
from core.logging import get_logger
from domain.incident_state import IncidentState

log = get_logger("blueprint.engine")


class PipelineAborted(Exception):
    """Pipeline abortado por falha em node com on_failure=ABORT."""


# F6-3 (NFR6): o orçamento de chamadas LLM é aplicado onde elas acontecem —
# ``adapters.llm.counting_client.CountingLLMClient``, injetado pelo
# ``app.builders.build_agent_orchestrator``. Antes existia aqui um
# ``increment_llm_calls(ctx)`` que nenhum caminho de produção chamava (o engine
# não vê as chamadas; elas ocorrem dentro do orchestrator). Alias mantido para
# os call sites que fazem ``except LLMCallLimitExceeded``.
LLMCallLimitExceeded = LLMBudgetExceeded


class BlueprintEngine:
    """Engine que executa PipelineDefinitions."""

    def __init__(
        self, config: Any = None, store: Any = None, creds: Any = None
    ) -> None:
        self.config = config
        self.store = store
        self.creds = creds
        # Resultados do run MAIS RECENTE (compat para quem lê após run/abort).
        # A lista autoritativa de cada run vai em state.metadata["_node_results"].
        self.results: list[NodeResult] = []

    def run(self, pipeline: PipelineDefinition, state: IncidentState) -> IncidentState:
        """Executa o pipeline completo.

        Args:
            pipeline: Definicao do pipeline com nodes.
            state: Estado inicial do incidente.

        Returns:
            Estado final apos execucao de todos os nodes.

        Raises:
            PipelineAborted: Se um node com on_failure=ABORT falhar.
            LLMCallLimitExceeded: Se exceder o orçamento de chamadas LLM
                (aplicado pelo ``CountingLLMClient`` do orchestrator; o engine
                apenas propaga — não cai em fallback).

        Limitações documentadas (F6-3):
            - ``pipeline_timeout_seconds`` é verificado ENTRE nodes; um node em
              execução não é interrompido. O orçamento efetivo do node agentic
              é ``max_llm_calls``.
            - O engine é síncrono e não emite ``LifecycleEvent``; os observers
              (Slack, demo, webhook runner) são alimentados pelas composition
              roots, que têm o loop async. Ver ``core/lifecycle.py``.
        """
        log.info(
            "pipeline.started",
            pipeline=pipeline.name,
            node_count=len(pipeline.nodes),
        )

        # F6-3: lista LOCAL por run → reentrante (A2A/MCP compartilham o engine
        # de app.state). ``self.results`` aponta para o run mais recente.
        results: list[NodeResult] = []
        self.results = results

        pipeline_start = time.monotonic()
        timeout_s = getattr(
            getattr(self.config, "blueprint", None), "pipeline_timeout_seconds", None
        )

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
                if (
                    node.node_type == NodeType.AGENTIC
                    and state.no_llm
                    and node.fallback
                ):
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

                results.append(result)
                log.info(
                    "node.completed",
                    node_id=node.id,
                    duration_ms=round(result.duration_ms, 1),
                )

            except LLMBudgetExceeded as exc:
                # Orçamento esgotado é condição do RUN, não falha do node: não
                # cai em fallback/skip — sobe para a composition root (exit 1).
                results.append(
                    NodeResult(
                        node_id=node.id,
                        success=False,
                        duration_ms=(time.monotonic() - start) * 1000,
                        error=str(exc),
                    )
                )
                log.error(
                    "pipeline.llm_budget_exceeded", node_id=node.id, error=str(exc)
                )
                raise
            except Exception as exc:
                duration = (time.monotonic() - start) * 1000
                log.error(
                    "node.failed",
                    node_id=node.id,
                    error=str(exc),
                    on_failure=node.on_failure.value,
                )

                if node.on_failure == OnFailure.ABORT:
                    results.append(
                        NodeResult(
                            node_id=node.id,
                            success=False,
                            duration_ms=duration,
                            error=str(exc),
                        )
                    )
                    raise PipelineAborted(
                        f"Node '{node.id}' failed and on_failure=ABORT: {exc}"
                    ) from exc

                elif node.on_failure == OnFailure.FALLBACK and node.fallback:
                    try:
                        state = node.fallback(state)
                        results.append(
                            NodeResult(
                                node_id=node.id,
                                success=True,
                                duration_ms=(time.monotonic() - start) * 1000,
                                used_fallback=True,
                            )
                        )
                        log.info("node.fallback_succeeded", node_id=node.id)
                    except Exception as fb_exc:
                        results.append(
                            NodeResult(
                                node_id=node.id,
                                success=False,
                                duration_ms=(time.monotonic() - start) * 1000,
                                error=f"Fallback also failed: {fb_exc}",
                            )
                        )
                        raise PipelineAborted(
                            f"Node '{node.id}' fallback also failed: {fb_exc}"
                        ) from fb_exc

                elif node.on_failure == OnFailure.SKIP:
                    results.append(
                        NodeResult(
                            node_id=node.id,
                            success=False,
                            duration_ms=duration,
                            error=str(exc),
                        )
                    )
                    log.info("node.skipped", node_id=node.id)
                    continue

        log.info(
            "pipeline.completed",
            pipeline=pipeline.name,
            nodes_run=len(results),
            all_success=all(r.success for r in results),
        )

        state = dataclasses.replace(
            state, metadata={**state.metadata, "_node_results": list(results)}
        )

        if self.store is not None:
            self.store.save(state.incident_id, state)

        return state
