"""
Testes para a ferramenta MCP diagnose_incident (Story 5.2 + F3-1).

Verifica normalização, dispatch para BlueprintEngine com o contrato REAL
run(pipeline, state) → IncidentState, e tratamento de erros.

F3-1: os mocks antigos usavam run(alert) com resultado de atributos planos,
mascarando o TypeError da assinatura errada e campos inexistentes no estado.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from domain.alert import NormalizedAlert
from domain.incident_state import IncidentState


def _make_alert(correlation_id: str = "inc-test") -> NormalizedAlert:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return NormalizedAlert(
        source="test",
        source_id="s1",
        correlation_id=correlation_id,
        title="Test",
        description="Test alert",
        severity="critical",
        alert_type="TestAlert",
        timestamp=now,
        detection_timestamp=now,
    )


def _make_result_state() -> IncidentState:
    """IncidentState real como o BlueprintEngine.run retorna."""
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return IncidentState(
        incident_id="inc-test",
        correlation_id="inc-test",
        alert=_make_alert(),
        received_at=now,
        started_at=now,
        diagnosis={
            "root_cause": "Connection pool exhausted after config deploy",
            "evidence": ["error_rate spike", "slow traces", "pool metrics"],
            "confidence": "high",
            "affected_services": ["payment-svc"],
        },
        proposed_action={
            "description": "Rollback config change",
            "command": "kubectl rollout undo deployment/payment-svc",
            "risk_level": "LOW",
            "rollback": None,
        },
        safety_verdict="APPROVED",
        safety_reasons=["PreValidator: LOW — auto_approved"],
    )


class _MockEngine:
    """BlueprintEngine mock com o contrato real run(pipeline, state)."""

    def run(self, pipeline, state):
        assert hasattr(pipeline, "nodes"), "engine.run deve receber PipelineDefinition"
        assert isinstance(state, IncidentState), "engine.run deve receber IncidentState"
        return _make_result_state()


class _MockEngineWithError:
    """BlueprintEngine que levanta exceção."""

    def run(self, pipeline, state):
        raise RuntimeError("Pipeline failed: FAISS not loaded")


class _MockNormalizerRegistry:
    """Normalizer registry mock que retorna NormalizedAlert real."""

    def normalize(self, source, payload):
        return _make_alert()


@pytest.mark.unit
class TestHandleDiagnoseIncident:
    """Testes para handle_diagnose_incident()."""

    @pytest.mark.asyncio
    async def test_valid_alert_returns_diagnosis(self):
        from interfaces.mcp.tools import handle_diagnose_incident

        result_json = await handle_diagnose_incident(
            arguments={
                "source": "pagerduty",
                "severity": "critical",
                "title": "Payment service down",
                "description": "Connection pool exhausted",
            },
            engine=_MockEngine(),
            normalizer_registry=_MockNormalizerRegistry(),
            incident_store=None,
        )
        data = json.loads(result_json)

        assert data["diagnosis"]["root_cause"] == (
            "Connection pool exhausted after config deploy"
        )
        assert data["evidence_chain"] == [
            "error_rate spike",
            "slow traces",
            "pool metrics",
        ]
        assert data["confidence"] == pytest.approx(0.85)
        assert data["proposed_fix"] == "Rollback config change"
        assert data["risk_assessment"]["safety_verdict"] == "APPROVED"
        assert data["correlation_id"]

    @pytest.mark.asyncio
    async def test_response_has_required_fields(self):
        from interfaces.mcp.tools import handle_diagnose_incident

        result_json = await handle_diagnose_incident(
            arguments={
                "source": "grafana",
                "severity": "high",
                "title": "High error rate",
                "description": "500 errors spiking",
            },
            engine=_MockEngine(),
            normalizer_registry=None,
            incident_store=None,
        )
        data = json.loads(result_json)

        required_fields = [
            "incident_id",
            "diagnosis",
            "evidence_chain",
            "confidence",
            "proposed_fix",
            "risk_assessment",
            "mode",
            "latency_ms",
            "correlation_id",
            "telemetry_source",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_invalid_severity_returns_validation_error(self):
        from interfaces.mcp.tools import handle_diagnose_incident

        result_json = await handle_diagnose_incident(
            arguments={
                "source": "pagerduty",
                "severity": "extreme",
                "title": "Test",
                "description": "Test",
            },
            engine=_MockEngine(),
            normalizer_registry=None,
            incident_store=None,
        )
        data = json.loads(result_json)
        assert data["type"] == "validation_error"
        assert data["status"] == 400
        assert "severity" in data["detail"].lower()

    @pytest.mark.asyncio
    async def test_missing_required_field_returns_error(self):
        from interfaces.mcp.tools import handle_diagnose_incident

        result_json = await handle_diagnose_incident(
            arguments={
                "source": "pagerduty",
                "severity": "critical",
            },
            engine=_MockEngine(),
            normalizer_registry=None,
            incident_store=None,
        )
        data = json.loads(result_json)
        assert data["type"] == "validation_error"

    @pytest.mark.asyncio
    async def test_engine_unavailable_returns_error(self):
        from interfaces.mcp.tools import handle_diagnose_incident

        result_json = await handle_diagnose_incident(
            arguments={
                "source": "pagerduty",
                "severity": "critical",
                "title": "Test",
                "description": "Test",
            },
            engine=None,
            normalizer_registry=None,
            incident_store=None,
        )
        data = json.loads(result_json)
        assert data["type"] == "engine_unavailable"
        assert data["status"] == 503

    @pytest.mark.asyncio
    async def test_pipeline_exception_returns_error_with_correlation_id(self):
        from interfaces.mcp.tools import handle_diagnose_incident

        result_json = await handle_diagnose_incident(
            arguments={
                "source": "pagerduty",
                "severity": "critical",
                "title": "Test",
                "description": "Test",
                "correlation_id": "test-corr-123",
            },
            engine=_MockEngineWithError(),
            normalizer_registry=None,
            incident_store=None,
        )
        data = json.loads(result_json)
        assert data["type"] == "pipeline_error"
        assert data["correlation_id"] == "test-corr-123"

    @pytest.mark.asyncio
    async def test_engine_run_called_with_pipeline_and_state(self):
        """F3-1: engine.run recebe (pipeline, IncidentState) — não o alerta cru."""
        from blueprint.nodes import PipelineDefinition
        from interfaces.mcp.tools import handle_diagnose_incident

        run_calls = []

        class TrackingEngine:
            def run(self, pipeline, state):
                run_calls.append((pipeline, state))
                return _make_result_state()

        await handle_diagnose_incident(
            arguments={
                "source": "databricks",
                "severity": "medium",
                "title": "Job failure",
                "description": "Spark job failed",
            },
            engine=TrackingEngine(),
            normalizer_registry=None,
            incident_store=None,
        )

        assert len(run_calls) == 1
        pipeline, state = run_calls[0]
        assert isinstance(pipeline, PipelineDefinition)
        assert isinstance(state, IncidentState)
        assert isinstance(state.alert, NormalizedAlert)
        assert state.no_llm is True

    @pytest.mark.asyncio
    async def test_nollm_mode_in_response(self):
        from interfaces.mcp.tools import handle_diagnose_incident

        result_json = await handle_diagnose_incident(
            arguments={
                "source": "grafana",
                "severity": "low",
                "title": "Test",
                "description": "Test",
            },
            engine=_MockEngine(),
            normalizer_registry=None,
            incident_store=None,
        )
        data = json.loads(result_json)
        assert data["mode"] == "heuristic"
        assert data["telemetry_source"] == "simulated-sample"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_diagnose_incident_real_engine_deterministic():
    """F3-1 E2E: engine real + pipeline real → diagnóstico em modo determinístico."""
    from app.builders import build_engine
    from config.loader import AppConfig
    from interfaces.mcp.tools import handle_diagnose_incident

    engine = build_engine(AppConfig())
    result_json = await handle_diagnose_incident(
        arguments={
            "source": "grafana",
            "severity": "critical",
            "title": "Payment service down",
            "description": "Connection pool exhausted after deploy",
        },
        engine=engine,
        normalizer_registry=None,
        incident_store=None,
    )
    data = json.loads(result_json)

    assert data.get("type") != "pipeline_error", data.get("detail")
    assert data["diagnosis"]["root_cause"]
    assert data["mode"] == "heuristic"
    assert data["telemetry_source"] == "simulated-sample"
    assert data["proposed_fix"]
    assert data["risk_assessment"]["safety_verdict"] in ("APPROVED", "NEEDS_REVIEW")
