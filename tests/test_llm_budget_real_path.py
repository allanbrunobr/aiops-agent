"""Correção pós-revisão #1: o orçamento LLM precisa atravessar o orchestrator.

A revisão de 2026-11-18 provou no M1 que ``except LLMBudgetExceeded: raise`` em
``blueprint/engine.py`` era inalcançável: os 5 call sites LLM do
``AgentOrchestrator`` (plan, reflect_on_step, replan, synthesize,
reflect_final) tinham ``except Exception`` que engolia a exceção e caía em
fallback — o run completava com diagnóstico heurístico em vez de abortar.

O teste antigo (``test_llm_budget.py``) usava um node fake que lançava a
exceção direto no engine. Estes testes usam o caminho de produção:
``build_agent_orchestrator`` + ``CountingLLMClient`` real + report real.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from adapters.llm.counting_client import CountingLLMClient, LLMBudgetExceeded
from app.builders import build_agent_orchestrator
from config.loader import AppConfig, BlueprintConfig

_PLAN = {
    "hypothesis": "pool exhaustion",
    "steps": [
        {
            "step_id": 1,
            "goal": "check pool",
            "tool": "query_logs",
            "tool_args": {"service": "payment-svc", "keyword": "pool"},
            "expected_signal": "exhausted",
        }
    ],
}


def _real_report():
    from data.sample_incident import create_incident
    from pipeline.aggregator import Aggregator

    inc = create_incident()
    return Aggregator().process(inc.logs, inc.metrics, inc.traces)


def _inner_client() -> MagicMock:
    """Provider fake: sempre responde um plano válido (nunca é o gargalo)."""
    inner = MagicMock(name="anthropic")
    inner.create_message.return_value = MagicMock(
        content=[MagicMock(text=json.dumps(_PLAN))]
    )
    return inner


def _orchestrator(tmp_path, max_calls: int):
    from rag.memory import IncidentMemoryDB

    cfg = AppConfig(blueprint=BlueprintConfig(max_llm_calls=max_calls))
    inner = _inner_client()
    orch = build_agent_orchestrator(
        cfg,
        client=inner,
        memory_db=IncidentMemoryDB(db_path=str(tmp_path / "m.db")),
    )
    assert isinstance(orch.client, CountingLLMClient)
    return orch, inner


@pytest.mark.unit
def test_orchestrator_run_propagates_budget_instead_of_heuristic(tmp_path):
    """max_calls=1: o plano consome o orçamento; a 2ª chamada precisa SUBIR."""
    orch, inner = _orchestrator(tmp_path, max_calls=1)
    heuristic = MagicMock(wraps=orch._diagnoser.heuristic_diagnosis)
    orch._diagnoser.heuristic_diagnosis = heuristic

    with pytest.raises(LLMBudgetExceeded):
        orch.run(_real_report(), None, detection={"is_incident": True})

    # o provider recebeu só a chamada dentro do orçamento
    assert inner.create_message.call_count == 1
    # e o diagnóstico NÃO foi fabricado pela heurística
    heuristic.assert_not_called()


def _plan_obj():
    from agents.orchestrator import InvestigationPlan, InvestigationStep

    step = InvestigationStep(
        step_id=1,
        goal="check pool",
        tool="query_logs",
        tool_args={"service": "payment-svc", "keyword": "pool"},
        expected_signal="exhausted",
        result="Connection pool exhausted (x40)",
    )
    return InvestigationPlan(hypothesis="pool exhaustion", steps=[step]), step


def _args(method: str, report):
    """Argumentos REAIS (não MagicMock) — assim a única exceção possível vem do
    create_message; um erro de formatação de prompt não mascara o resultado."""
    from agents.skill_router import DEFAULT_SKILL

    plan, step = _plan_obj()
    return {
        "_generate_plan": (report, DEFAULT_SKILL, []),
        "_reflect_on_step": (plan, step, report),
        "_replan": (plan, {"reason": "no signal"}, report, DEFAULT_SKILL),
        "_synthesize_diagnosis": (plan, report, {}, DEFAULT_SKILL),
        "_reflect_final": ({"root_cause": "pool"}, True, ["query_logs"], plan),
    }[method]


@pytest.mark.unit
@pytest.mark.parametrize(
    "method",
    [
        "_generate_plan",
        "_reflect_on_step",
        "_replan",
        "_synthesize_diagnosis",
        "_reflect_final",
    ],
)
def test_each_llm_call_site_reraises_budget(tmp_path, method):
    """Os 5 handlers: estouro de orçamento SOBE em vez de virar fallback."""
    orch, inner = _orchestrator(tmp_path, max_calls=1)
    orch.client.calls = orch.client.max_calls  # orçamento já esgotado
    with pytest.raises(LLMBudgetExceeded):
        getattr(orch, method)(*_args(method, _real_report()))
    inner.create_message.assert_not_called()  # nada além do orçamento


@pytest.mark.unit
@pytest.mark.parametrize(
    "method",
    [
        "_generate_plan",
        "_reflect_on_step",
        "_replan",
        "_synthesize_diagnosis",
        "_reflect_final",
    ],
)
def test_each_call_site_still_degrades_on_ordinary_errors(tmp_path, method):
    """Sanidade: erro comum do provider continua no fallback (não regrediu)."""
    orch, inner = _orchestrator(tmp_path, max_calls=15)
    inner.create_message.side_effect = RuntimeError("provider 500")
    getattr(orch, method)(*_args(method, _real_report()))  # não levanta


@pytest.mark.unit
def test_engine_aborts_through_real_diagnoser_node(tmp_path, monkeypatch):
    """Fim a fim no V1: node agentic real → orchestrator real → engine propaga."""
    from blueprint.adapters import pipeline_nodes
    from blueprint.engine import BlueprintEngine, LLMCallLimitExceeded
    from blueprint.nodes import BlueprintNode, NodeType, OnFailure, PipelineDefinition
    from domain.incident_state import IncidentState

    cfg = AppConfig(blueprint=BlueprintConfig(max_llm_calls=1))
    orch, inner = _orchestrator(tmp_path, max_calls=1)
    monkeypatch.setattr(
        "app.builders.build_agent_orchestrator", lambda config, **kw: orch
    )

    fallback_called = []

    def fallback(state):
        fallback_called.append(True)
        return state

    node = BlueprintNode(
        id="diagnoser",
        node_type=NodeType.AGENTIC,
        execute=pipeline_nodes.make_diagnoser_node(cfg),
        on_failure=OnFailure.FALLBACK,
        fallback=fallback,
    )
    now = datetime.now(timezone.utc)
    state = IncidentState(
        incident_id="inc-1",
        correlation_id="inc-1",
        alert=None,
        received_at=now,
        started_at=now,
        no_llm=False,
        no_rag=True,
        aggregated_report={"_report_obj": _real_report()},
    )
    engine = BlueprintEngine(config=cfg)
    with pytest.raises(LLMCallLimitExceeded):
        engine.run(PipelineDefinition(name="t", nodes=[node]), state)

    assert fallback_called == []
    assert inner.create_message.call_count == 1
