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

ACTION = MitigationAction(
    description="rollback",
    command="kubectl rollout undo deployment/api",
    risk_level="LOW",
    rollback_command="kubectl rollout undo deployment/api",
)


def _snap(error_rate, availability):
    return HealthSnapshot(
        timestamp=datetime.now(),
        error_rate=error_rate,
        latency_p99_ms=200.0,
        availability=availability,
        connection_pool_active=10,
        connection_pool_max=50,
    )


@pytest.mark.unit
class TestMitigatorTnr:
    def test_without_measurement_is_not_verified(self):
        report = MitigationAgent().verify_with_tnr({"action": ACTION}, TNRSafetyGuard())
        assert report.result == TNRResult.NOT_VERIFIED

    def test_with_real_snapshots_detects_regression(self):
        report = MitigationAgent().verify_with_tnr(
            {"action": ACTION},
            TNRSafetyGuard(),
            pre_health=_snap(1.0, 99.0),
            post_health=_snap(40.0, 50.0),
        )
        assert report.result == TNRResult.REGRESSION

    def test_with_real_snapshots_detects_improvement(self):
        report = MitigationAgent().verify_with_tnr(
            {"action": ACTION},
            TNRSafetyGuard(),
            pre_health=_snap(40.0, 50.0),
            post_health=_snap(1.0, 99.0),
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
            msg(
                {
                    "hypothesis": "h",
                    "steps": [
                        {
                            "step_id": 1,
                            "goal": "g",
                            "tool": "query_metrics",
                            "tool_args": {},
                        }
                    ],
                }
            ),
            msg({"decision": "finalize", "assessment": "a"}),
            msg(
                {
                    "root_cause": "pool exhausted",
                    "affected_services": ["api"],
                    "confidence": "high",
                }
            ),
            msg({"lesson": "l", "score": 0.5, "priority_tools": []}),
        ]
        orch = AgentOrchestrator(
            client=client, model="m", memory_db=None, skill_router=None
        )
        with (
            patch("agents.orchestrator.DetectionAgent") as det,
            patch("agents.orchestrator.MitigationAgent") as mit,
            patch.object(AgentOrchestrator, "_execute_step", return_value="r"),
        ):
            det.return_value.detect.return_value = {"is_incident": True}
            mit.return_value.propose_safe.return_value = {"action": ACTION}
            mit.return_value.simulate_tnr_verification.return_value = MagicMock(
                result=TNRResult.NOT_VERIFIED,
                health_delta=0.0,
                message="TNR not verified: no post-action health measurement",
                verification_mode="simulated",
            )
            report = MagicMock(affected_services={"api"}, log_templates=[])
            report.to_compact_text.return_value = "t"
            outcome = orch.run(report, None)

        assert outcome.tnr_result == "not_verified"
        assert outcome.replans == 0  # "não verificado" não é regressão


@pytest.mark.unit
class TestCliVerificationStage:
    def test_heuristic_path_reports_not_verified_and_is_not_a_regression(self):
        import io
        from argparse import Namespace
        from contextlib import redirect_stderr, redirect_stdout

        from app.builders import build_engine, build_pipeline
        from blueprint.adapters.state_adapter import state_to_result
        from config.loader import AppConfig
        from data.sample_incident import create_incident
        from interfaces.cli.v1_runner import create_incident_state

        incident = create_incident()
        state = create_incident_state(
            Namespace(
                no_llm=True,
                no_rag=True,
                scenario=None,
                output_format="markdown",
                compact=True,
            ),
            {
                "logs": incident.logs,
                "metrics": incident.metrics,
                "traces": incident.traces,
            },
            incident=incident,
        )
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            final = build_engine(AppConfig()).run(build_pipeline(AppConfig()), state)
        result = state_to_result(final)
        assert result["stages"]["verification"]["result"] == "not_verified"
        assert "rollback" not in result["stages"]


@pytest.mark.unit
class TestSafetyOrchestratorTnrLayer:
    def _orch(self, tnr=None, sanitizer=None):
        from safety.orchestrator import SafetyOrchestrator

        return SafetyOrchestrator(tnr=tnr, sanitizer=sanitizer)

    def test_without_snapshots_is_not_verified_and_not_blocking(self):
        result = self._orch().validate(
            ACTION, {"confidence": "high", "affected_services": ["api"]}
        )
        tnr = result.layer_details["tnr"]
        # D-TNR: sem medição pós-ação, a projeção benigna é NOT_VERIFIED
        # (TNRReport, não dict) e não bloqueia.
        assert tnr.result == TNRResult.NOT_VERIFIED
        assert "no post-action health measurement" in tnr.message
        assert "tnr" not in result.blocked_by

    def test_tnr_exception_fails_closed(self):
        broken = MagicMock()
        broken.simulate_check.side_effect = RuntimeError("tnr down")
        result = self._orch(tnr=broken).validate(
            ACTION,
            {"confidence": "high"},
            system_state={
                "pre_health": _snap(1.0, 99.0),
                "post_health": _snap(1.0, 99.0),
            },
        )
        assert "tnr" in result.blocked_by

    def test_real_snapshots_regression_blocks(self):
        result = self._orch().validate(
            ACTION,
            {"confidence": "high"},
            system_state={
                "pre_health": _snap(1.0, 99.0),
                "post_health": _snap(40.0, 50.0),
            },
        )
        assert "tnr" in result.blocked_by

    def test_sanitizer_exception_fails_closed(self):
        broken = MagicMock()
        broken.sanitize_text.side_effect = RuntimeError("sanitizer down")
        result = self._orch(sanitizer=broken).validate(ACTION, {"confidence": "high"})
        assert "sanitizer" in result.blocked_by
