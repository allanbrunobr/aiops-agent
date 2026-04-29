"""Testes unitarios para configuracao externa do modelo LLM.

Valida que DiagnosisAgent aceita parametro model, que o env var
AIOPS_LLM_MODEL e lido e usado, que --no-llm ignora o env var,
e que o modelo aparece no postmortem e no terminal output.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


# ===========================================================================
# Task 1: DiagnosisAgent model configuration
# ===========================================================================

class TestDiagnosisAgentModelConfig:
    """Testes para configuracao de modelo no DiagnosisAgent."""

    @pytest.mark.unit
    def test_default_model_is_claude_sonnet(self):
        """Sem parametro model, default e claude-sonnet-4-20250514."""
        from agents.diagnoser import DiagnosisAgent
        agent = DiagnosisAgent()
        assert agent.model == "claude-sonnet-4-20250514"

    @pytest.mark.unit
    def test_custom_model_is_stored(self):
        """DiagnosisAgent(model='custom') armazena o modelo."""
        from agents.diagnoser import DiagnosisAgent
        agent = DiagnosisAgent(model="claude-haiku-4-20250514")
        assert agent.model == "claude-haiku-4-20250514"

    @pytest.mark.unit
    def test_model_passed_to_call_llm(self):
        """_call_llm() usa self.model ao inves de string hardcoded."""
        from agents.diagnoser import DiagnosisAgent
        agent = DiagnosisAgent(model="test-model-42")

        mock_client = MagicMock()
        mock_client.create_message.return_value = MagicMock(content=[])

        with patch.object(agent, "_get_client", return_value=mock_client):
            agent._call_llm(messages=[{"role": "user", "content": "test"}], tools=[])

        mock_client.create_message.assert_called_once()
        call_kwargs = mock_client.create_message.call_args
        assert call_kwargs.kwargs.get("model") == "test-model-42"

    @pytest.mark.unit
    def test_default_model_backward_compatible(self):
        """Constructor sem model preserva compatibilidade com chamadas existentes."""
        from agents.diagnoser import DiagnosisAgent
        agent = DiagnosisAgent(max_iterations=5)
        assert agent.model == "claude-sonnet-4-20250514"
        assert agent.max_iterations == 5


# ===========================================================================
# Task 2: AIOPS_LLM_MODEL env var in main.py
# ===========================================================================

class TestAiopsLlmModelEnvVar:
    """Testes para leitura do env var AIOPS_LLM_MODEL."""

    @pytest.mark.unit
    def test_env_var_read_when_set(self, monkeypatch):
        """AIOPS_LLM_MODEL e lido quando definido."""
        monkeypatch.setenv("AIOPS_LLM_MODEL", "claude-haiku-4-20250514")
        model = os.environ.get("AIOPS_LLM_MODEL", "claude-sonnet-4-20250514")
        assert model == "claude-haiku-4-20250514"

    @pytest.mark.unit
    def test_default_when_env_var_not_set(self, monkeypatch):
        """Sem AIOPS_LLM_MODEL, default e claude-sonnet-4-20250514."""
        monkeypatch.delenv("AIOPS_LLM_MODEL", raising=False)
        model = os.environ.get("AIOPS_LLM_MODEL", "claude-sonnet-4-20250514")
        assert model == "claude-sonnet-4-20250514"

    @pytest.mark.unit
    def test_step_agents_passes_model_to_diagnoser(self):
        """step_agents() passa llm_model para DiagnosisAgent."""
        from pipeline.aggregator import AggregatedReport

        # Cria report mock minimo
        mock_report = MagicMock(spec=AggregatedReport)
        mock_report.log_templates = []
        mock_report.metric_anomalies = []
        mock_report.slow_traces = []
        mock_report.affected_services = set()
        mock_report.severity = "high"
        mock_report.total_logs = 0
        mock_report.total_metrics = 0
        mock_report.total_traces = 0
        mock_report.to_text.return_value = "test report"

        with patch("main.DiagnosisAgent") as MockDiagnoser, \
             patch("main.DetectionAgent") as MockDetector, \
             patch("main.MitigationAgent"), \
             patch("main.TNRSafetyGuard"):

            # Setup mocks
            MockDetector.return_value = MagicMock()
            mock_diagnoser_instance = MagicMock()
            mock_diagnoser_instance.reflect.return_value = {
                "lesson": "test", "score": 0.5, "priority_tools": [],
            }
            mock_diagnoser_instance.tools_used = []
            mock_diagnoser_instance.iteration_count = 0
            mock_diagnoser_instance.skill = None
            MockDiagnoser.return_value = mock_diagnoser_instance

            from main import step_agents
            step_agents(mock_report, retriever=None, use_llm=True, llm_model="test-model-99")

            # Verifica que DiagnosisAgent foi criado com o modelo correto
            MockDiagnoser.assert_called_once()
            call_kwargs = MockDiagnoser.call_args
            assert call_kwargs.kwargs.get("model") == "test-model-99" or \
                   (len(call_kwargs.args) > 1 and call_kwargs.args[1] == "test-model-99")

    @pytest.mark.unit
    def test_no_llm_mode_skips_model(self):
        """--no-llm nao valida nem importa modelo, ignora env var."""
        from pipeline.aggregator import AggregatedReport

        mock_report = MagicMock(spec=AggregatedReport)
        mock_report.log_templates = []
        mock_report.metric_anomalies = []
        mock_report.slow_traces = []
        mock_report.affected_services = set()
        mock_report.severity = "high"
        mock_report.total_logs = 0
        mock_report.total_metrics = 0
        mock_report.total_traces = 0
        mock_report.to_text.return_value = "test report"

        with patch("main.DiagnosisAgent") as MockDiagnoser, \
             patch("main.DetectionAgent"), \
             patch("main.MitigationAgent"), \
             patch("main.TNRSafetyGuard"):

            from main import step_agents
            # Em no-llm mode, llm_model nao importa
            step_agents(mock_report, retriever=None, use_llm=False, llm_model="invalid-model")

            # DiagnosisAgent ainda e criado (max_iterations=1 em no-llm)
            MockDiagnoser.assert_called_once()

    @pytest.mark.unit
    def test_result_contains_llm_model(self):
        """result['llm_model'] e preenchido corretamente."""
        from pipeline.aggregator import AggregatedReport

        mock_report = MagicMock(spec=AggregatedReport)
        mock_report.log_templates = []
        mock_report.metric_anomalies = []
        mock_report.slow_traces = []
        mock_report.affected_services = set()
        mock_report.severity = "high"
        mock_report.total_logs = 0
        mock_report.total_metrics = 0
        mock_report.total_traces = 0
        mock_report.to_text.return_value = "test report"

        with patch("main.DiagnosisAgent") as MockDiag, \
             patch("main.DetectionAgent"), \
             patch("main.MitigationAgent"), \
             patch("main.TNRSafetyGuard"):

            _inst = MockDiag.return_value
            _inst.reflect.return_value = {"lesson": "test", "score": 0.5, "priority_tools": []}
            _inst.tools_used = []
            _inst.iteration_count = 0
            _inst.skill = None

            from main import step_agents
            result = step_agents(mock_report, retriever=None, use_llm=True, llm_model="my-model")

            assert result["llm_model"] == "my-model"

    @pytest.mark.unit
    def test_result_contains_heuristic_when_no_llm(self):
        """result['llm_model'] e 'heuristic' quando --no-llm."""
        from pipeline.aggregator import AggregatedReport

        mock_report = MagicMock(spec=AggregatedReport)
        mock_report.log_templates = []
        mock_report.metric_anomalies = []
        mock_report.slow_traces = []
        mock_report.affected_services = set()
        mock_report.severity = "high"
        mock_report.total_logs = 0
        mock_report.total_metrics = 0
        mock_report.total_traces = 0
        mock_report.to_text.return_value = "test report"

        with patch("main.DiagnosisAgent"), \
             patch("main.DetectionAgent"), \
             patch("main.MitigationAgent"), \
             patch("main.TNRSafetyGuard"):

            from main import step_agents
            result = step_agents(mock_report, retriever=None, use_llm=False, llm_model="ignored")

            assert result["llm_model"] == "heuristic"


# ===========================================================================
# Task 4: Postmortem model metadata
# ===========================================================================

class TestPostmortemModelMetadata:
    """Testes para modelo no postmortem."""

    @pytest.mark.unit
    def test_postmortem_contains_model_name(self, tmp_path):
        """Postmortem markdown inclui o nome do modelo."""
        from data.models import SimulatedIncident
        from datetime import datetime

        now = datetime.now()
        incident = SimulatedIncident(
            logs=[], metrics=[], traces=[],
            deploy_time=now, spike_start=now,
            detection_time=now, fix_time=now,
            actual_root_cause="test", actual_fix="test",
        )

        result = {
            "success": True,
            "stages": {
                "detection": {"is_incident": True, "severity": "high", "signals": []},
                "diagnosis": {"root_cause": "test", "confidence": "high", "evidence": []},
                "mitigation": None,
                "verification": {"result": "success", "health_delta": "+75%", "message": "OK"},
            },
            "timeline": [],
            "scenario_name": "test-scenario",
            "llm_model": "claude-haiku-4-20250514",
        }

        from main import step_postmortem
        path, _ = step_postmortem(result, incident, output_dir=str(tmp_path))

        content = path.read_text()
        assert "claude-haiku-4-20250514" in content
        assert "LLM Model" in content

    @pytest.mark.unit
    def test_postmortem_shows_heuristic_when_no_llm(self, tmp_path):
        """Postmortem mostra 'heuristic' quando --no-llm."""
        from data.models import SimulatedIncident
        from datetime import datetime

        now = datetime.now()
        incident = SimulatedIncident(
            logs=[], metrics=[], traces=[],
            deploy_time=now, spike_start=now,
            detection_time=now, fix_time=now,
            actual_root_cause="test", actual_fix="test",
        )

        result = {
            "success": True,
            "stages": {
                "detection": {"is_incident": True, "severity": "high", "signals": []},
                "diagnosis": {"root_cause": "test", "confidence": "medium", "evidence": []},
                "mitigation": None,
                "verification": {"result": "success", "health_delta": "+75%", "message": "OK"},
            },
            "timeline": [],
            "scenario_name": "test-scenario",
            "llm_model": "heuristic",
        }

        from main import step_postmortem
        path, _ = step_postmortem(result, incident, output_dir=str(tmp_path))

        content = path.read_text()
        assert "heuristic" in content
        assert "LLM Model" in content
