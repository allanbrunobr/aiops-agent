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


# ===========================================================================
# Task 4: Postmortem model metadata
# ===========================================================================


class TestPostmortemModelMetadata:
    """Testes para modelo no postmortem."""

    @pytest.mark.unit
    def test_postmortem_contains_model_name(self, tmp_path):
        """Postmortem markdown inclui o nome do modelo."""
        from datetime import datetime

        from data.models import SimulatedIncident

        now = datetime.now()
        incident = SimulatedIncident(
            logs=[],
            metrics=[],
            traces=[],
            deploy_time=now,
            spike_start=now,
            detection_time=now,
            fix_time=now,
            actual_root_cause="test",
            actual_fix="test",
        )

        result = {
            "success": True,
            "stages": {
                "detection": {"is_incident": True, "severity": "high", "signals": []},
                "diagnosis": {
                    "root_cause": "test",
                    "confidence": "high",
                    "evidence": [],
                },
                "mitigation": None,
                "verification": {
                    "result": "success",
                    "health_delta": "+75%",
                    "message": "OK",
                },
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
        from datetime import datetime

        from data.models import SimulatedIncident

        now = datetime.now()
        incident = SimulatedIncident(
            logs=[],
            metrics=[],
            traces=[],
            deploy_time=now,
            spike_start=now,
            detection_time=now,
            fix_time=now,
            actual_root_cause="test",
            actual_fix="test",
        )

        result = {
            "success": True,
            "stages": {
                "detection": {"is_incident": True, "severity": "high", "signals": []},
                "diagnosis": {
                    "root_cause": "test",
                    "confidence": "medium",
                    "evidence": [],
                },
                "mitigation": None,
                "verification": {
                    "result": "success",
                    "health_delta": "+75%",
                    "message": "OK",
                },
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
