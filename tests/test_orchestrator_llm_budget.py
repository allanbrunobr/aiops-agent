"""config.blueprint.max_llm_calls limita de fato as chamadas ao LLM por execução.

Pendência do P1 (docs/p1-progresso.md, item 1): o limite existia na config
(NFR6, padrão 15), mas só BlueprintEngine.increment_llm_calls o lia, e esse
método só era chamado em testes. Com o laço corrigido, o pior caso medido
ainda era 29 chamadas: replan no 8º step de cada plano.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.orchestrator import AgentOrchestrator
from config.loader import AppConfig

STEPS = [{"step_id": i, "goal": "g", "tool": "query_metrics", "tool_args": {}} for i in range(1, 9)]


def _msg(payload: dict) -> MagicMock:
    m = MagicMock()
    m.content = [MagicMock(text=json.dumps(payload))]
    return m


def _adversarial_client() -> MagicMock:
    """Pior caso: planos de 8 steps e replan no 8º step de cada plano."""
    state = {"reflections": 0}

    def create_message(**kwargs):
        content = kwargs["messages"][0]["content"]
        if content.startswith(("You are an SRE agent planning", "An incident investigation plan failed")):
            state["reflections"] = 0
            return _msg({"hypothesis": "h", "steps": STEPS})
        if content.startswith("You are evaluating one step"):
            state["reflections"] += 1
            decision = "replan" if state["reflections"] == 8 else "continue"
            return _msg({"decision": decision, "reason": "r", "assessment": "a"})
        if content.startswith("Synthesize a final incident diagnosis"):
            return _msg({"root_cause": "pool exhausted", "confidence": "high"})
        return _msg({"lesson": "l", "score": 0.5, "priority_tools": []})

    client = MagicMock()
    client.create_message.side_effect = create_message
    return client


def _run(orch: AgentOrchestrator):
    with patch("agents.orchestrator.DetectionAgent") as det, \
         patch("agents.orchestrator.MitigationAgent") as mit, \
         patch("agents.orchestrator.TNRSafetyGuard"), \
         patch.object(AgentOrchestrator, "_execute_step", return_value="r"):
        det.return_value.detect.return_value = {"is_incident": True}
        mit.return_value.verify_with_tnr.return_value = MagicMock(result=MagicMock(value="success"))
        report = MagicMock(affected_services={"svc"}, log_templates=[])
        report.to_compact_text.return_value = "t"
        return orch.run(report, None)


def _config(max_calls: int) -> AppConfig:
    return AppConfig(blueprint={"max_llm_calls": max_calls})


@pytest.mark.unit
class TestLlmCallBudget:
    def test_adversarial_run_respects_default_limit_of_config(self):
        client = _adversarial_client()
        orch = AgentOrchestrator(client=client, model="m", memory_db=None,
                                 skill_router=None, config=AppConfig())
        outcome = _run(orch)
        assert client.create_message.call_count <= AppConfig().blueprint.max_llm_calls
        assert outcome.diagnosis.get("root_cause")

    @pytest.mark.parametrize("limit", [1, 5, 10])
    def test_custom_limit_is_enforced(self, limit):
        client = _adversarial_client()
        orch = AgentOrchestrator(client=client, model="m", memory_db=None,
                                 skill_router=None, config=_config(limit))
        outcome = _run(orch)
        assert client.create_message.call_count <= limit
        assert outcome.diagnosis.get("root_cause")  # heurística quando o orçamento acaba
        assert outcome.llm_calls == client.create_message.call_count
        assert outcome.llm_budget_exhausted is True

    @pytest.mark.parametrize("limit", [1, 2, 15])
    def test_one_call_is_reserved_for_synthesis(self, limit):
        """A investigação não pode gastar o orçamento todo: o diagnóstico final
        sai do LLM (root_cause do mock), não da heurística."""
        client = _adversarial_client()
        orch = AgentOrchestrator(client=client, model="m", memory_db=None,
                                 skill_router=None, config=_config(limit))
        outcome = _run(orch)
        assert outcome.diagnosis.get("root_cause") == "pool exhausted"
        assert client.create_message.call_count <= limit

    def test_no_config_uses_default_limit(self):
        client = _adversarial_client()
        orch = AgentOrchestrator(client=client, model="m", memory_db=None, skill_router=None)
        _run(orch)
        assert client.create_message.call_count <= AgentOrchestrator.DEFAULT_MAX_LLM_CALLS

    def test_budget_is_per_run(self):
        client = _adversarial_client()
        orch = AgentOrchestrator(client=client, model="m", memory_db=None,
                                 skill_router=None, config=_config(5))
        _run(orch)
        first = client.create_message.call_count
        _run(orch)
        assert client.create_message.call_count - first <= 5
        assert client.create_message.call_count - first > 0  # a 2ª execução não começa esgotada

    def test_client_is_not_replaced(self):
        """main.py reusa _orchestrator.client no SkillEvolver, fora do orçamento do run."""
        client = _adversarial_client()
        orch = AgentOrchestrator(client=client, model="m", memory_db=None,
                                 skill_router=None, config=_config(3))
        _run(orch)
        assert orch.client is client

    def test_normal_run_is_not_marked_exhausted(self):
        client = MagicMock()
        client.create_message.side_effect = [
            _msg({"hypothesis": "h", "steps": STEPS[:1]}),
            _msg({"decision": "finalize", "assessment": "a"}),
            _msg({"root_cause": "pool exhausted", "confidence": "high"}),
            _msg({"lesson": "l", "score": 0.5, "priority_tools": []}),
        ]
        orch = AgentOrchestrator(client=client, model="m", memory_db=None,
                                 skill_router=None, config=AppConfig())
        outcome = _run(orch)
        assert outcome.llm_calls == 4
        assert outcome.llm_budget_exhausted is False
