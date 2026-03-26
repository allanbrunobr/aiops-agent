"""
Testes unitários para o AgentOrchestrator — Estágio 2.

Cobre:
  - Dataclasses (InvestigationStep, InvestigationPlan, AgentOutcome)
  - Prompts (placeholders obrigatórios)
  - _generate_plan (happy path, tool inválida filtrada, fallback JSON inválido)
  - _execute_step (retorna string)
  - _reflect_on_step (decisions válidas, fallback JSON inválido)
  - _replan (novo plano, fallback JSON inválido)
  - _synthesize_diagnosis (happy path, fallback heurístico)
  - run() (completo, no-incident, TNR regression → replan)
"""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from agents.orchestrator import (
    FINAL_REFLECT_PROMPT,
    PLAN_PROMPT,
    REPLAN_PROMPT,
    STEP_REFLECT_PROMPT,
    SYNTHESIZE_PROMPT,
    AgentOrchestrator,
    AgentOutcome,
    InvestigationPlan,
    InvestigationStep,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_client(response: dict) -> MagicMock:
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps(response))]
    client = MagicMock()
    client.messages.create.return_value = msg
    return client


def _mock_client_seq(responses: list[dict]) -> MagicMock:
    """Client que retorna respostas em sequência."""
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        msg = MagicMock()
        msg.content = [MagicMock(text=json.dumps(responses[min(call_count, len(responses) - 1)]))]
        call_count += 1
        return msg

    client = MagicMock()
    client.messages.create.side_effect = side_effect
    return client


def _make_report(text: str = "CPU spike on payment-svc") -> MagicMock:
    report = MagicMock()
    report.to_compact_text.return_value = text
    report.log_templates = []
    report.affected_services = {"payment-svc"}
    return report


def _make_skill(name: str = "test-skill") -> MagicMock:
    from rag.memory import DiagnosticSkill
    return DiagnosticSkill(
        skill_id="s1",
        name=name,
        scenario_pattern=".*",
        system_prompt="Test prompt for testing",
        priority_tools=[],
    )


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDataclasses:
    def test_investigation_step_defaults(self):
        step = InvestigationStep(
            step_id=1, goal="check metrics", tool="query_metrics",
            tool_args={}, expected_signal="spike",
        )
        assert step.result is None
        assert step.reflection is None
        assert step.skip_condition == ""

    def test_investigation_plan_defaults(self):
        plan = InvestigationPlan(hypothesis="memory leak", steps=[])
        assert plan.confidence_threshold == 0.8
        assert plan.max_steps == 5
        assert plan.steps == []

    def test_agent_outcome_defaults(self):
        plan = InvestigationPlan(hypothesis="h", steps=[])
        outcome = AgentOutcome(diagnosis={}, plan=plan)
        assert outcome.tools_used == []
        assert outcome.iterations == 0
        assert outcome.replans == 0
        assert outcome.tnr_passed is False
        assert outcome.reflection is None


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPrompts:
    def test_plan_prompt_placeholders(self):
        for ph in [
            "{incident_summary}", "{available_tools}", "{skill_name}",
            "{utility_score}", "{total_uses}", "{skill_prompt_summary}",
            "{similar_incidents}", "{recent_reflections}",
        ]:
            assert ph in PLAN_PROMPT, f"PLAN_PROMPT missing {ph}"

    def test_step_reflect_prompt_placeholders(self):
        for ph in [
            "{hypothesis}", "{step_goal}", "{step_tool}",
            "{step_result}", "{expected_signal}", "{remaining_steps}",
        ]:
            assert ph in STEP_REFLECT_PROMPT, f"STEP_REFLECT_PROMPT missing {ph}"

    def test_replan_prompt_placeholders(self):
        for ph in [
            "{original_hypothesis}", "{executed_steps}",
            "{failure_reason}", "{incident_summary}", "{skill_guidance}",
        ]:
            assert ph in REPLAN_PROMPT, f"REPLAN_PROMPT missing {ph}"

    def test_synthesize_prompt_placeholders(self):
        for ph in ["{hypothesis}", "{evidence}", "{incident_summary}"]:
            assert ph in SYNTHESIZE_PROMPT

    def test_final_reflect_prompt_placeholders(self):
        for ph in ["{diagnosis}", "{tnr_result}", "{tools_used}", "{plan_steps}", "{hypothesis}"]:
            assert ph in FINAL_REFLECT_PROMPT


# ---------------------------------------------------------------------------
# Init e helpers
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOrchestratorInit:
    def test_instantiates_with_none_client(self):
        orch = AgentOrchestrator(client=None, model="test-model", memory_db=None, skill_router=None)
        assert orch.client is None
        assert orch.model == "test-model"
        assert orch.MAX_REPLANS == 2

    def test_list_tools_contains_all_known_tools(self):
        orch = AgentOrchestrator(client=None, model="m", memory_db=None, skill_router=None)
        tools = orch._list_tools()
        for name in [
            "query_logs", "query_metrics", "query_traces",
            "search_similar", "correlate_signals",
            "reconstruct_timeline", "classify_fault_type",
        ]:
            assert name in tools


# ---------------------------------------------------------------------------
# _generate_plan
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGeneratePlan:
    def test_returns_investigation_plan(self):
        payload = {
            "hypothesis": "Memory leak in payment-svc",
            "steps": [
                {"step_id": 1, "goal": "Check memory", "tool": "query_metrics",
                 "tool_args": {"service": "payment-svc", "metric_name": "memory"},
                 "expected_signal": "OOM events", "skip_condition": ""},
            ],
            "confidence_threshold": 0.8,
            "max_steps": 3,
        }
        orch = AgentOrchestrator(client=_mock_client(payload), model="t", memory_db=None, skill_router=None)
        plan = orch._generate_plan(_make_report(), _make_skill(), [])
        assert plan.hypothesis == "Memory leak in payment-svc"
        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "query_metrics"

    def test_filters_invalid_tool_names(self):
        payload = {
            "hypothesis": "h",
            "steps": [
                {"step_id": 1, "goal": "g", "tool": "nonexistent_tool", "tool_args": {}, "expected_signal": ""},
                {"step_id": 2, "goal": "g2", "tool": "query_logs", "tool_args": {}, "expected_signal": ""},
            ],
            "confidence_threshold": 0.8,
            "max_steps": 5,
        }
        orch = AgentOrchestrator(client=_mock_client(payload), model="t", memory_db=None, skill_router=None)
        plan = orch._generate_plan(_make_report(), _make_skill(), [])
        assert len(plan.steps) == 1
        assert plan.steps[0].tool == "query_logs"

    def test_fallback_on_json_error(self):
        msg = MagicMock()
        msg.content = [MagicMock(text="not json")]
        client = MagicMock()
        client.messages.create.return_value = msg
        orch = AgentOrchestrator(client=client, model="t", memory_db=None, skill_router=None)
        plan = orch._generate_plan(_make_report(), _make_skill(), [])
        assert isinstance(plan, InvestigationPlan)
        assert len(plan.steps) >= 1

    def test_fallback_when_all_steps_invalid(self):
        """Plano sem nenhum step válido deve retornar plano default."""
        payload = {
            "hypothesis": "h",
            "steps": [
                {"step_id": 1, "goal": "g", "tool": "bad_tool", "tool_args": {}, "expected_signal": ""},
            ],
            "confidence_threshold": 0.8,
            "max_steps": 5,
        }
        orch = AgentOrchestrator(client=_mock_client(payload), model="t", memory_db=None, skill_router=None)
        plan = orch._generate_plan(_make_report(), _make_skill(), [])
        # default plan usa classify_fault_type e correlate_signals
        tools = {s.tool for s in plan.steps}
        assert tools & {"classify_fault_type", "correlate_signals"}


# ---------------------------------------------------------------------------
# _execute_step
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExecuteStep:
    def test_returns_string(self):
        orch = AgentOrchestrator(client=None, model="t", memory_db=None, skill_router=None)
        step = InvestigationStep(
            step_id=1, goal="g", tool="query_logs",
            tool_args={"service": "svc", "keyword": "error"},
            expected_signal="errors",
        )
        report = MagicMock()
        report.log_templates = []
        result = orch._execute_step(step, report, retriever=None)
        assert isinstance(result, str)

    def test_returns_error_string_on_exception(self):
        """Se a tool lançar exceção, deve retornar string descrevendo o erro."""
        orch = AgentOrchestrator(client=None, model="t", memory_db=None, skill_router=None)
        step = InvestigationStep(
            step_id=1, goal="g", tool="query_logs",
            tool_args={}, expected_signal="",
        )
        with patch("agents.orchestrator.DiagnosisAgent") as MockDiag:
            MockDiag.return_value._execute_tool.side_effect = RuntimeError("boom")
            result = orch._execute_step(step, MagicMock(), None)
        assert "Tool execution failed" in result


# ---------------------------------------------------------------------------
# _reflect_on_step
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReflectOnStep:
    def test_returns_valid_decision(self):
        orch = AgentOrchestrator(
            client=_mock_client({"decision": "continue", "assessment": "ok", "confidence": 0.7}),
            model="t", memory_db=None, skill_router=None,
        )
        plan = InvestigationPlan(hypothesis="h", steps=[])
        step = InvestigationStep(step_id=1, goal="g", tool="query_logs",
                                 tool_args={}, expected_signal="e", result="logs")
        result = orch._reflect_on_step(plan, step, MagicMock())
        assert result["decision"] in ("continue", "replan", "finalize")

    def test_fallback_on_bad_json(self):
        msg = MagicMock()
        msg.content = [MagicMock(text="not json")]
        client = MagicMock()
        client.messages.create.return_value = msg
        orch = AgentOrchestrator(client=client, model="t", memory_db=None, skill_router=None)
        plan = InvestigationPlan(hypothesis="h", steps=[])
        step = InvestigationStep(step_id=1, goal="g", tool="query_logs",
                                 tool_args={}, expected_signal="e", result="r")
        result = orch._reflect_on_step(plan, step, MagicMock())
        assert result["decision"] == "continue"

    def test_finalize_decision(self):
        orch = AgentOrchestrator(
            client=_mock_client({"decision": "finalize", "assessment": "evidence found", "confidence": 0.9}),
            model="t", memory_db=None, skill_router=None,
        )
        plan = InvestigationPlan(hypothesis="h", steps=[])
        step = InvestigationStep(step_id=1, goal="g", tool="query_metrics",
                                 tool_args={}, expected_signal="e", result="memory spike")
        result = orch._reflect_on_step(plan, step, MagicMock())
        assert result["decision"] == "finalize"


# ---------------------------------------------------------------------------
# _replan
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReplan:
    def test_returns_new_plan(self):
        payload = {
            "hypothesis": "config change caused the issue",
            "steps": [
                {"step_id": 1, "goal": "check deploys", "tool": "query_logs",
                 "tool_args": {"service": "svc", "keyword": "deploy"},
                 "expected_signal": "deploy event"},
            ],
        }
        orch = AgentOrchestrator(client=_mock_client(payload), model="t", memory_db=None, skill_router=None)
        old_plan = InvestigationPlan(
            hypothesis="memory leak",
            steps=[InvestigationStep(step_id=1, goal="g", tool="query_metrics",
                                     tool_args={}, expected_signal="e", result="no spike")],
        )
        new = orch._replan(old_plan, {"reason": "no memory evidence"}, _make_report(), _make_skill())
        assert new.hypothesis == "config change caused the issue"
        assert len(new.steps) == 1

    def test_fallback_on_bad_json(self):
        msg = MagicMock()
        msg.content = [MagicMock(text="not json")]
        client = MagicMock()
        client.messages.create.return_value = msg
        orch = AgentOrchestrator(client=client, model="t", memory_db=None, skill_router=None)
        old_plan = InvestigationPlan(hypothesis="original", steps=[])
        result = orch._replan(old_plan, {}, _make_report(), _make_skill())
        assert result.hypothesis == "original"


# ---------------------------------------------------------------------------
# _synthesize_diagnosis
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSynthesizeDiagnosis:
    def test_returns_dict_with_root_cause(self):
        payload = {
            "root_cause": "Connection pool exhausted",
            "evidence": ["pool_size=5"],
            "affected_services": ["payment-svc"],
            "confidence": "high",
            "timeline": "Deploy → pool reduction → errors",
        }
        orch = AgentOrchestrator(client=_mock_client(payload), model="t", memory_db=None, skill_router=None)
        plan = InvestigationPlan(
            hypothesis="pool exhaustion",
            steps=[InvestigationStep(step_id=1, goal="g", tool="query_metrics",
                                     tool_args={}, expected_signal="e",
                                     result="pool_size dropped to 5")],
        )
        result = orch._synthesize_diagnosis(plan, _make_report(), {})
        assert result["root_cause"] == "Connection pool exhausted"

    def test_fallback_on_bad_json(self):
        msg = MagicMock()
        msg.content = [MagicMock(text="not json")]
        client = MagicMock()
        client.messages.create.return_value = msg
        orch = AgentOrchestrator(client=client, model="t", memory_db=None, skill_router=None)
        plan = InvestigationPlan(hypothesis="h", steps=[])
        report = MagicMock()
        report.to_compact_text.return_value = ""
        report.log_templates = []
        report.metrics = {}
        report.traces = []
        report.affected_services = set()
        result = orch._synthesize_diagnosis(plan, report, {})
        assert isinstance(result, dict)
        assert "root_cause" in result


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOrchestratorRun:
    def _setup_db_and_router(self, db_path: str):
        from rag.memory import IncidentMemoryDB
        from agents.skill_router import SkillRouter
        db = IncidentMemoryDB(db_path=db_path)
        router = SkillRouter(db)
        router.seed_default_skills()
        return db, router

    def test_run_returns_agent_outcome(self):
        responses = [
            # _generate_plan
            {"hypothesis": "pool exhaustion", "steps": [
                {"step_id": 1, "goal": "check pool", "tool": "query_metrics",
                 "tool_args": {"service": "svc", "metric_name": "connection_pool"},
                 "expected_signal": "pool drop"},
            ], "confidence_threshold": 0.8, "max_steps": 3},
            # _reflect_on_step → finalize
            {"decision": "finalize", "assessment": "pool evidence found", "confidence": 0.9},
            # _synthesize_diagnosis
            {"root_cause": "pool exhausted", "evidence": ["pool=5"],
             "affected_services": ["svc"], "confidence": "high", "timeline": "t"},
            # _reflect_final
            {"lesson": "check pool first", "score": 0.8, "priority_tools": ["query_metrics"]},
        ]

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            db, router = self._setup_db_and_router(db_path)
            with patch("agents.orchestrator.AgentOrchestrator._execute_step", return_value="pool_size=5"), \
                 patch("agents.orchestrator.DetectionAgent") as MockDet, \
                 patch("agents.orchestrator.MitigationAgent") as MockMit, \
                 patch("agents.orchestrator.TNRSafetyGuard"):

                MockDet.return_value.detect.return_value = {"is_incident": True}
                MockMit.return_value.propose.return_value = {"action": "rollback"}
                tnr_ok = MagicMock()
                tnr_ok.result.value = "success"
                tnr_ok.message = "ok"
                MockMit.return_value.verify_with_tnr.return_value = tnr_ok

                orch = AgentOrchestrator(
                    client=_mock_client_seq(responses),
                    model="test", memory_db=db, skill_router=router,
                )
                outcome = orch.run(_make_report(), retriever=None)
                assert isinstance(outcome, AgentOutcome)
                assert "root_cause" in outcome.diagnosis
                assert outcome.tnr_passed is True
        finally:
            os.unlink(db_path)

    def test_run_no_incident_returns_early(self):
        with patch("agents.orchestrator.DetectionAgent") as MockDet:
            MockDet.return_value.detect.return_value = {"is_incident": False}
            orch = AgentOrchestrator(client=None, model="t", memory_db=None, skill_router=None)
            report = MagicMock()
            report.log_templates = []
            report.affected_services = set()
            outcome = orch.run(report, retriever=None)
            assert outcome.diagnosis.get("is_incident") is False

    def test_tnr_regression_triggers_replan(self):
        """TNR regression deve incrementar replans e tentar nova investigação."""
        responses = [
            # plan inicial
            {"hypothesis": "h", "steps": [
                {"step_id": 1, "goal": "g", "tool": "query_metrics",
                 "tool_args": {}, "expected_signal": "e"},
            ], "confidence_threshold": 0.8, "max_steps": 3},
            # reflect → finalize
            {"decision": "finalize", "assessment": "a", "confidence": 0.9},
            # synthesize
            {"root_cause": "pool exhausted", "evidence": [], "affected_services": [], "confidence": "high", "timeline": "t"},
            # reflect_final (antes do replan)
            {"lesson": "l", "score": 0.5, "priority_tools": []},
            # replan após TNR regression
            {"hypothesis": "h2", "steps": [
                {"step_id": 1, "goal": "g2", "tool": "query_logs",
                 "tool_args": {}, "expected_signal": "e2"},
            ]},
            # synthesize após replan
            {"root_cause": "root2", "evidence": [], "affected_services": [], "confidence": "medium", "timeline": "t2"},
            # reflect_final final
            {"lesson": "l2", "score": 0.6, "priority_tools": []},
        ]

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            db, router = self._setup_db_and_router(db_path)
            with patch("agents.orchestrator.AgentOrchestrator._execute_step", return_value="r"), \
                 patch("agents.orchestrator.DetectionAgent") as MockDet, \
                 patch("agents.orchestrator.MitigationAgent") as MockMit, \
                 patch("agents.orchestrator.TNRSafetyGuard"):

                MockDet.return_value.detect.return_value = {"is_incident": True}
                MockMit.return_value.propose.return_value = {"action": "rollback"}

                tnr_fail = MagicMock()
                tnr_fail.result.value = "regression"
                tnr_fail.message = "health degraded"
                tnr_ok = MagicMock()
                tnr_ok.result.value = "success"
                tnr_ok.message = "ok"
                MockMit.return_value.verify_with_tnr.side_effect = [tnr_fail, tnr_ok]

                orch = AgentOrchestrator(
                    client=_mock_client_seq(responses),
                    model="test", memory_db=db, skill_router=router,
                )
                outcome = orch.run(_make_report(), retriever=None)
                assert outcome.replans >= 1
                assert outcome.tnr_passed is True
        finally:
            os.unlink(db_path)
