"""
Testes para a ferramenta MCP diagnose_incident (Story 5.2).

Verifica normalização, dispatch para BlueprintEngine, e tratamento de erros.
"""

from __future__ import annotations

import json

import pytest


class _MockEngine:
    """BlueprintEngine mock que retorna diagnóstico estruturado."""

    def run(self, alert):
        class Result:
            diagnosis = "Connection pool exhausted after config deploy"
            evidence_chain = ["error_rate spike", "slow traces", "pool metrics"]
            confidence = 0.87
            proposed_fix = "kubectl rollout undo deployment/payment-svc"
            risk_assessment = {"risk": "LOW", "blast_radius": 1}
            mode = "heuristic"

        return Result()


class _MockEngineWithError:
    """BlueprintEngine que levanta exceção."""

    def run(self, alert):
        raise RuntimeError("Pipeline failed: FAISS not loaded")


class _MockNormalizerRegistry:
    """Normalizer registry mock."""

    def normalize(self, source, payload):
        return payload  # retorna payload diretamente como "normalizado"


@pytest.mark.unit
class TestHandleDiagnoseIncident:
    """Testes para handle_diagnose_incident()."""

    @pytest.mark.asyncio
    async def test_valid_alert_returns_diagnosis(self):
        """Verifica que alerta válido retorna diagnóstico estruturado."""
        from mcp_server.tools import handle_diagnose_incident

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

        assert "diagnosis" in data
        assert "evidence_chain" in data
        assert "confidence" in data
        assert "proposed_fix" in data
        assert "risk_assessment" in data
        assert "correlation_id" in data

    @pytest.mark.asyncio
    async def test_response_has_required_fields(self):
        """Verifica que todos os campos obrigatórios estão na resposta."""
        from mcp_server.tools import handle_diagnose_incident

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
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_invalid_severity_returns_validation_error(self):
        """Verifica que severidade inválida retorna erro RFC 7807."""
        from mcp_server.tools import handle_diagnose_incident

        result_json = await handle_diagnose_incident(
            arguments={
                "source": "pagerduty",
                "severity": "extreme",  # inválido
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
        """Verifica que campo obrigatório faltando retorna erro."""
        from mcp_server.tools import handle_diagnose_incident

        result_json = await handle_diagnose_incident(
            arguments={
                "source": "pagerduty",
                "severity": "critical",
                # Faltando title e description
            },
            engine=_MockEngine(),
            normalizer_registry=None,
            incident_store=None,
        )
        data = json.loads(result_json)
        assert data["type"] == "validation_error"

    @pytest.mark.asyncio
    async def test_engine_unavailable_returns_error(self):
        """Verifica que engine=None retorna erro gracioso."""
        from mcp_server.tools import handle_diagnose_incident

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
        """Verifica que exceção do pipeline retorna RFC 7807 com correlation_id."""
        from mcp_server.tools import handle_diagnose_incident

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
    async def test_engine_run_called_with_normalized_alert(self):
        """Verifica que BlueprintEngine.run() é chamado (mesmo caminho que webhook)."""
        from mcp_server.tools import handle_diagnose_incident

        run_calls = []

        class TrackingEngine:
            def run(self, alert):
                run_calls.append(alert)

                class Result:
                    diagnosis = "test"
                    evidence_chain = []
                    confidence = 0.5
                    proposed_fix = ""
                    risk_assessment = {}
                    mode = "heuristic"

                return Result()

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

    @pytest.mark.asyncio
    async def test_nollm_mode_in_response(self):
        """Verifica que mode='heuristic' é incluído na resposta."""
        from mcp_server.tools import handle_diagnose_incident

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
        assert "mode" in data
        assert data["mode"] in ("heuristic", "llm")
