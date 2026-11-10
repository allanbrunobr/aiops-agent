"""Uma falha do MitigationAgent não derruba a execução: vira revisão manual.

Pendência do P1 (docs/p1-progresso.md, item 4): `mitigator.propose` era
chamado sem proteção em AgentOrchestrator.run (2 pontos) e no caminho
heurístico de main.step_agents. Qualquer exceção ali — como o KeyError do
template de CPU (NOVO-01) — derrubava a execução inteira.
"""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import pytest

from agents.mitigator import MitigationAgent
from agents.orchestrator import AgentOrchestrator
from safety.pre_validator import ApprovalStatus, PreExecutionValidator

DIAGNOSIS = {"root_cause": "pool exhausted", "affected_services": ["payment-svc"],
             "confidence": "high"}


def _msg(payload: dict) -> MagicMock:
    m = MagicMock()
    m.content = [MagicMock(text=json.dumps(payload))]
    return m


def _assert_manual_review(mitigation: dict):
    assert mitigation["pattern_matched"] == "proposal_failed"
    action = mitigation["action"]
    assert action.command == ""
    assert action.risk_level == "HIGH"
    r = PreExecutionValidator().validate(action, DIAGNOSIS)
    assert r.approval_status != ApprovalStatus.AUTO_APPROVED
    assert r.requires_human is True


def _run_orchestrator(tnr_ok: bool = True):
    client = MagicMock()
    client.create_message.side_effect = [
        _msg({"hypothesis": "h", "steps": [
            {"step_id": 1, "goal": "g", "tool": "query_metrics", "tool_args": {}}]}),
        _msg({"decision": "finalize", "assessment": "a"}),
        _msg(DIAGNOSIS),
    ] + [_msg({"lesson": "l", "score": 0.5, "hypothesis": "h", "steps": [],
               **DIAGNOSIS})] * 10
    orch = AgentOrchestrator(client=client, model="m", memory_db=None, skill_router=None)
    with patch("agents.orchestrator.DetectionAgent") as det, \
         patch("agents.orchestrator.TNRSafetyGuard"), \
         patch.object(MitigationAgent, "verify_with_tnr",
                      return_value=MagicMock(result=MagicMock(
                          value="success" if tnr_ok else "regression"))), \
         patch.object(AgentOrchestrator, "_execute_step", return_value="r"):
        det.return_value.detect.return_value = {"is_incident": True}
        report = MagicMock(affected_services={"payment-svc"}, log_templates=[])
        report.to_compact_text.return_value = "t"
        return orch.run(report, None)


@pytest.mark.unit
class TestOrchestratorSurvivesMitigatorFailure:
    def test_first_proposal_failure_becomes_manual_review(self):
        with patch.object(MitigationAgent, "propose", side_effect=KeyError("current+2")):
            outcome = _run_orchestrator()
        _assert_manual_review(outcome.mitigation)
        assert outcome.diagnosis["root_cause"] == "pool exhausted"

    def test_failure_after_tnr_replan_becomes_manual_review(self):
        ok = {"action": MagicMock(command="kubectl rollout undo deployment/payment-svc"),
              "pattern_matched": "connection pool", "diagnosis": DIAGNOSIS}
        with patch.object(MitigationAgent, "propose",
                          side_effect=[ok, RuntimeError("template broke")]):
            outcome = _run_orchestrator(tnr_ok=False)
        _assert_manual_review(outcome.mitigation)


@pytest.mark.unit
class TestProposeSafe:
    def test_success_is_unchanged(self):
        agent = MitigationAgent()
        assert agent.propose_safe(DIAGNOSIS) == agent.propose(DIAGNOSIS)

    def test_failure_is_logged(self, caplog):
        with patch.object(MitigationAgent, "propose", side_effect=RuntimeError("boom")), \
             caplog.at_level("WARNING"):
            out = MitigationAgent().propose_safe(DIAGNOSIS)
        _assert_manual_review(out)
        assert any("boom" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
class TestMainHeuristicPathSurvivesMitigatorFailure:
    def test_step_agents_without_llm(self):
        import main
        from data.sample_incident import create_incident

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            report = main.step_pipeline(create_incident())
            with patch.object(MitigationAgent, "propose", side_effect=RuntimeError("boom")):
                result = main.step_agents(report, None, use_llm=False)
        _assert_manual_review(result["stages"]["mitigation"])


@pytest.mark.unit
class TestBlueprintFallbackSurvivesMitigatorFailure:
    def test_v1_no_llm_pipeline_sends_manual_review_to_safety(self, tmp_path):
        from argparse import Namespace

        from app.builders import build_engine, build_pipeline
        from config.loader import AppConfig
        from main import create_incident, create_incident_state

        incident = create_incident()
        state = create_incident_state(
            Namespace(no_llm=True, no_rag=True, scenario=None,
                      output_format="markdown", compact=True),
            {"logs": incident.logs, "metrics": incident.metrics, "traces": incident.traces},
        )
        with patch.object(MitigationAgent, "propose", side_effect=RuntimeError("boom")), \
             redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            final = build_engine(AppConfig()).run(build_pipeline(AppConfig()), state)

        assert final.proposed_action["command"] == ""
        assert final.safety_verdict == "NEEDS_REVIEW"
