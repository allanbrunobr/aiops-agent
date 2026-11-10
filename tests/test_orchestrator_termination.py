"""AgentOrchestrator.run() sempre termina, com número limitado de chamadas ao LLM.

Regressão de F01 (docs/analise-20261109.md): quando toda reflexão devolvia
"continue" — o que acontece em QUALQUER falha do LLM, porque o fallback de
_reflect_on_step é "continue" — o while repetia o mesmo plano sem fim.

O cliente falso tem orçamento de chamadas: ao estourar, levanta uma
BaseException, que escapa dos `except Exception` do orchestrator. Assim uma
regressão vira falha imediata em vez de pendurar a suíte.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.orchestrator import AgentOrchestrator, InvestigationPlan, InvestigationStep

CALL_BUDGET = 50


class _CallBudgetExceeded(BaseException):
    """BaseException de propósito: não é engolida por `except Exception`."""


def _budgeted_client(behavior) -> MagicMock:
    """Cliente cujo create_message aplica `behavior` até estourar o orçamento."""
    client = MagicMock()

    def create_message(**kwargs):
        if client.create_message.call_count > CALL_BUDGET:
            raise _CallBudgetExceeded(f"mais de {CALL_BUDGET} chamadas ao LLM: laço sem saída")
        return behavior()

    client.create_message.side_effect = create_message
    return client


def _raise():
    raise RuntimeError("401 invalid api key")


def _report() -> MagicMock:
    report = MagicMock()
    report.to_compact_text.return_value = "latency spike on payment-svc"
    report.log_templates = []
    report.affected_services = {"payment-svc"}
    return report


def _msg(payload) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=payload if isinstance(payload, str) else json.dumps(payload))]
    return msg


def _run(orch: AgentOrchestrator):
    with patch("agents.orchestrator.DetectionAgent") as det, \
         patch("agents.orchestrator.MitigationAgent") as mit, \
         patch("agents.orchestrator.TNRSafetyGuard"), \
         patch.object(AgentOrchestrator, "_execute_step", return_value="no signal"):
        det.return_value.detect.return_value = {"is_incident": True}
        mit.return_value.propose.return_value = MagicMock()
        mit.return_value.verify_with_tnr.return_value = MagicMock(result="SUCCESS")
        return orch.run(_report(), None)


@pytest.mark.unit
class TestRunAlwaysTerminates:
    def test_llm_always_failing_terminates_with_heuristic_diagnosis(self):
        """Chave inválida / rede fora: toda chamada levanta."""
        client = _budgeted_client(_raise)
        orch = AgentOrchestrator(client=client, model="m", memory_db=None, skill_router=None)

        outcome = _run(orch)

        assert outcome.diagnosis.get("root_cause")
        # plano + 1 reflexão por step do plano default + síntese + reflexão final
        assert client.create_message.call_count <= 10

    def test_llm_always_continue_terminates(self):
        """LLM que nunca decide finalize/replan."""
        client = _budgeted_client(lambda: _msg({"decision": "continue", "assessment": "meh"}))
        orch = AgentOrchestrator(client=client, model="m", memory_db=None, skill_router=None)

        outcome = _run(orch)

        assert outcome.diagnosis.get("root_cause")
        assert client.create_message.call_count <= 10

    def test_json_inside_code_fences_terminates(self):
        """json.loads falha em ```json ...```: vira 'continue' em toda reflexão."""
        client = _budgeted_client(lambda: _msg('```json\n{"decision": "finalize"}\n```'))
        orch = AgentOrchestrator(client=client, model="m", memory_db=None, skill_router=None)

        outcome = _run(orch)

        assert outcome.diagnosis.get("root_cause")


@pytest.mark.unit
class TestBoundedPlans:
    def test_plan_from_llm_is_capped(self):
        """Um plano com 1000 steps não pode virar 1000 chamadas de reflexão."""
        steps = [
            {"step_id": i, "goal": "g", "tool": "query_metrics", "tool_args": {}}
            for i in range(1000)
        ]
        client = MagicMock()
        client.create_message.return_value = _msg({"hypothesis": "h", "steps": steps})
        orch = AgentOrchestrator(client=client, model="m", memory_db=None, skill_router=None)
        skill = MagicMock(utility_score=0.5, total_uses=0, system_prompt="p", last_reflection="")
        skill.name = "s"

        plan = orch._generate_plan(_report(), skill, [])

        assert len(plan.steps) <= AgentOrchestrator.MAX_STEPS_PER_PLAN

    def test_replan_from_llm_is_capped(self):
        steps = [
            {"step_id": i, "goal": "g", "tool": "query_metrics", "tool_args": {}}
            for i in range(1000)
        ]
        client = MagicMock()
        client.create_message.return_value = _msg({"hypothesis": "h", "steps": steps})
        orch = AgentOrchestrator(client=client, model="m", memory_db=None, skill_router=None)
        old = InvestigationPlan(
            hypothesis="old",
            steps=[InvestigationStep(
                step_id=1, goal="g", tool="query_metrics", tool_args={}, expected_signal="",
            )],
        )

        plan = orch._replan(old, {"reason": "x"}, _report(), MagicMock(system_prompt="p"))

        assert len(plan.steps) <= AgentOrchestrator.MAX_STEPS_PER_PLAN


@pytest.mark.unit
class TestSynthesisShape:
    @pytest.mark.parametrize("payload", [{}, [], {"evidence": []}, "null"])
    def test_synthesis_without_root_cause_falls_back_to_heuristic(self, payload):
        """{} mantinha o while girando; lista quebrava diagnosis.get no main."""
        client = MagicMock()
        client.create_message.return_value = _msg(payload)
        orch = AgentOrchestrator(client=client, model="m", memory_db=None, skill_router=None)
        plan = InvestigationPlan(hypothesis="h", steps=[])

        with patch("agents.orchestrator.DiagnosisAgent") as diag:
            diag.return_value._heuristic_diagnosis.return_value = {"root_cause": "heuristic"}
            result = orch._synthesize_diagnosis(plan, _report(), {}, None)

        assert result == {"root_cause": "heuristic"}


@pytest.mark.unit
class TestAnthropicClientLimits:
    def test_factory_sets_explicit_timeout_and_retries(self):
        with patch("adapters.llm.anthropic_client.Anthropic") as sdk:
            from adapters.llm.anthropic_client import create_anthropic_llm_client
            create_anthropic_llm_client(api_key="k")
        kwargs = sdk.call_args.kwargs
        assert isinstance(kwargs.get("timeout"), (int, float)) and kwargs["timeout"] > 0
        assert kwargs.get("max_retries") is not None
