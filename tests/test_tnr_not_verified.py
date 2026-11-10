"""TNR sem medição pós-ação responde "não verificado", e exceção falha fechado.

P2 — S5: F05, SEC-07, F09 (docs/analise-20261109.md):
- MitigationAgent.verify_with_tnr comparava snapshots pré/pós FIXOS (uma
  melhora inventada) e sempre dava SUCCESS — o replan por regressão e o
  EXIT_REGRESSION eram código morto;
- SafetyOrchestrator comparava o baseline com ele mesmo (sempre SUCCESS) e
  tratava exceção do TNR e do sanitizer como aprovado.
Nada no projeto executa a ação, então não existe medição pós-ação real.
"""
from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from agents.mitigator import MitigationAgent
from domain.incident import MitigationAction
from safety.tnr import HealthSnapshot, TNRResult, TNRSafetyGuard

ACTION = MitigationAction(description="rollback", command="kubectl rollout undo deployment/api",
                          risk_level="LOW", rollback_command="kubectl rollout undo deployment/api")


def _snap(error_rate, availability):
    return HealthSnapshot(timestamp=datetime.now(), error_rate=error_rate, latency_p99_ms=200.0,
                          availability=availability, connection_pool_active=10,
                          connection_pool_max=50)


@pytest.mark.unit
class TestMitigatorTnr:
    def test_without_measurement_is_not_verified(self):
        report = MitigationAgent().verify_with_tnr({"action": ACTION}, TNRSafetyGuard())
        assert report.result == TNRResult.NOT_VERIFIED

    def test_with_real_snapshots_detects_regression(self):
        report = MitigationAgent().verify_with_tnr(
            {"action": ACTION}, TNRSafetyGuard(),
            pre_health=_snap(1.0, 99.0), post_health=_snap(40.0, 50.0),
        )
        assert report.result == TNRResult.REGRESSION

    def test_with_real_snapshots_detects_improvement(self):
        report = MitigationAgent().verify_with_tnr(
            {"action": ACTION}, TNRSafetyGuard(),
            pre_health=_snap(40.0, 50.0), post_health=_snap(1.0, 99.0),
        )
        assert report.result == TNRResult.SUCCESS


@pytest.mark.unit
class TestOrchestratorDoesNotTreatNotVerifiedAsRegression:
    def test_no_replan_and_result_exposed(self):
        from agents.orchestrator import AgentOrchestrator

        def msg(d):
            m = MagicMock()
            m.content = [MagicMock(text=json.dumps(d))]
            return m

        client = MagicMock()
        client.create_message.side_effect = [
            msg({"hypothesis": "h", "steps": [
                {"step_id": 1, "goal": "g", "tool": "query_metrics", "tool_args": {}}]}),
            msg({"decision": "finalize", "assessment": "a"}),
            msg({"root_cause": "pool exhausted", "affected_services": ["api"], "confidence": "high"}),
            msg({"lesson": "l", "score": 0.5, "priority_tools": []}),
        ]
        orch = AgentOrchestrator(client=client, model="m", memory_db=None, skill_router=None)
        with patch("agents.orchestrator.DetectionAgent") as det, \
             patch.object(AgentOrchestrator, "_execute_step", return_value="r"):
            det.return_value.detect.return_value = {"is_incident": True}
            report = MagicMock(affected_services={"api"}, log_templates=[])
            report.to_compact_text.return_value = "t"
            outcome = orch.run(report, None)

        assert outcome.tnr_result == "not_verified"
        assert outcome.replans == 0  # "não verificado" não é regressão


@pytest.mark.unit
class TestCliVerificationStage:
    def test_heuristic_path_reports_not_verified_and_is_not_a_regression(self):
        import io
        from contextlib import redirect_stdout

        import main
        from data.sample_incident import create_incident

        with redirect_stdout(io.StringIO()):
            report = main.step_pipeline(create_incident())
            result = main.step_agents(report, None, use_llm=False)
        assert result["stages"]["verification"]["result"] == "not_verified"
        assert "rollback" not in result["stages"]


@pytest.mark.unit
class TestSafetyOrchestratorTnrLayer:
    def _orch(self, tnr=None, sanitizer=None):
        from safety.orchestrator import SafetyOrchestrator
        return SafetyOrchestrator(tnr=tnr, sanitizer=sanitizer)

    def test_without_snapshots_is_not_verified_and_not_blocking(self):
        result = self._orch().validate(ACTION, {"confidence": "high", "affected_services": ["api"]})
        assert result.layer_details["tnr"] == {"verified": False,
                                               "reason": "no post-action health measurement"}
        assert "tnr" not in result.blocked_by

    def test_tnr_exception_fails_closed(self):
        broken = MagicMock()
        broken.simulate_check.side_effect = RuntimeError("tnr down")
        result = self._orch(tnr=broken).validate(
            ACTION, {"confidence": "high"},
            system_state={"pre_health": _snap(1.0, 99.0), "post_health": _snap(1.0, 99.0)},
        )
        assert "tnr" in result.blocked_by

    def test_real_snapshots_regression_blocks(self):
        result = self._orch().validate(
            ACTION, {"confidence": "high"},
            system_state={"pre_health": _snap(1.0, 99.0), "post_health": _snap(40.0, 50.0)},
        )
        assert "tnr" in result.blocked_by

    def test_sanitizer_exception_fails_closed(self):
        broken = MagicMock()
        broken.sanitize_text.side_effect = RuntimeError("sanitizer down")
        result = self._orch(sanitizer=broken).validate(ACTION, {"confidence": "high"})
        assert "sanitizer" in result.blocked_by
