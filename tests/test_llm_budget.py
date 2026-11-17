"""F6-3 (NFR6): orçamento de chamadas LLM aplicado de verdade.

``BlueprintEngine.increment_llm_calls`` existia e era testado, mas nenhum
caminho de produção o chamava — o limite ``blueprint.max_llm_calls`` era
decorativo. Agora ``CountingLLMClient`` envolve o cliente que o orchestrator
usa e o engine propaga a exceção (não a transforma em fallback).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from adapters.llm.counting_client import CountingLLMClient, LLMBudgetExceeded
from app.builders import build_agent_orchestrator
from blueprint.engine import BlueprintEngine, LLMCallLimitExceeded, PipelineAborted
from blueprint.nodes import BlueprintNode, NodeType, OnFailure, PipelineDefinition
from config.loader import AppConfig, BlueprintConfig
from domain.incident_state import IncidentState


@pytest.mark.unit
def test_counting_client_delegates_and_counts():
    inner = MagicMock()
    inner.create_message.return_value = "resp"
    c = CountingLLMClient(inner, max_calls=3)

    assert c.create_message(model="m") == "resp"
    assert c.calls == 1 and c.remaining == 2
    inner.create_message.assert_called_once_with(model="m")


@pytest.mark.unit
def test_counting_client_blocks_before_sending_the_extra_call():
    inner = MagicMock()
    c = CountingLLMClient(inner, max_calls=2)
    c.create_message()
    c.create_message()
    with pytest.raises(LLMBudgetExceeded, match="3 > 2"):
        c.create_message()
    # a 3ª chamada NÃO chegou ao provider
    assert inner.create_message.call_count == 2


@pytest.mark.unit
def test_counting_client_rejects_non_positive_budget():
    with pytest.raises(ValueError):
        CountingLLMClient(MagicMock(), max_calls=0)


@pytest.mark.unit
def test_engine_alias_is_the_adapter_exception():
    """v1_runner faz `except LLMCallLimitExceeded` — precisa pegar a do adapter."""
    assert LLMCallLimitExceeded is LLMBudgetExceeded


@pytest.mark.unit
def test_builder_wraps_client_with_config_budget(tmp_path):
    from rag.memory import IncidentMemoryDB

    cfg = AppConfig(blueprint=BlueprintConfig(max_llm_calls=4))
    raw = MagicMock(name="llm")
    orch = build_agent_orchestrator(
        cfg,
        client=raw,
        memory_db=IncidentMemoryDB(db_path=str(tmp_path / "m.db")),
        seed_skills=False,
    )
    assert isinstance(orch.client, CountingLLMClient)
    assert orch.client.max_calls == 4
    assert orch.client._inner is raw


@pytest.mark.unit
def test_builder_does_not_double_wrap(tmp_path):
    from rag.memory import IncidentMemoryDB

    cfg = AppConfig(blueprint=BlueprintConfig(max_llm_calls=4))
    already = CountingLLMClient(MagicMock(), max_calls=9)
    orch = build_agent_orchestrator(
        cfg,
        client=already,
        memory_db=IncidentMemoryDB(db_path=str(tmp_path / "m.db")),
        seed_skills=False,
    )
    assert orch.client is already and orch.client.max_calls == 9


def _state() -> IncidentState:
    now = datetime.now(timezone.utc)
    return IncidentState(
        incident_id="inc-1",
        correlation_id="inc-1",
        alert=None,
        received_at=now,
        started_at=now,
        no_llm=False,
    )


@pytest.mark.unit
def test_engine_propagates_budget_exception_instead_of_fallback():
    """Antes: a exceção caía em `except Exception` → fallback heurístico silencioso."""
    fallback_called = []

    def agentic(state):
        raise LLMBudgetExceeded("LLM call limit exceeded: 16 > 15")

    def fallback(state):
        fallback_called.append(True)
        return state

    node = BlueprintNode(
        id="diagnoser",
        node_type=NodeType.AGENTIC,
        execute=agentic,
        on_failure=OnFailure.FALLBACK,
        fallback=fallback,
    )
    engine = BlueprintEngine(config=AppConfig())
    with pytest.raises(LLMCallLimitExceeded):
        engine.run(PipelineDefinition(name="t", nodes=[node]), _state())

    assert fallback_called == []  # não mascarou o estouro de orçamento
    assert engine.results[-1].success is False
    assert "limit exceeded" in (engine.results[-1].error or "")


@pytest.mark.unit
def test_other_node_errors_still_follow_on_failure_policy():
    """Sanidade: a nova cláusula não engole o comportamento ABORT normal."""

    def boom(state):
        raise RuntimeError("x")

    node = BlueprintNode(
        id="n",
        node_type=NodeType.DETERMINISTIC,
        execute=boom,
        on_failure=OnFailure.ABORT,
    )
    with pytest.raises(PipelineAborted):
        BlueprintEngine(config=AppConfig()).run(
            PipelineDefinition(name="t", nodes=[node]), _state()
        )
