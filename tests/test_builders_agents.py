"""F5-1: builders de agentes em app/builders.py (Fase 5 do plano de refatoração).

Antes, o node agentic do Blueprint e a CLI V0 repetiam o wiring de
DetectionAgent/DiagnosisAgent/AgentOrchestrator — e com thresholds/modelo
divergentes (orchestrator detectava com defaults; CLI com config).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.builders import (
    build_agent_orchestrator,
    build_detector_agent,
    build_diagnoser_agent,
    resolve_llm_model,
)
from config.loader import AppConfig, LLMConfig, ThresholdsConfig


def _config(**thresholds) -> AppConfig:
    return AppConfig(
        thresholds=ThresholdsConfig(**thresholds),
        llm=LLMConfig(model_name="cfg-model"),
    )


@pytest.mark.unit
def test_resolve_llm_model_env_beats_config(monkeypatch):
    monkeypatch.setenv("AIOPS_LLM_MODEL", "env-model")
    assert resolve_llm_model(_config()) == "env-model"


@pytest.mark.unit
def test_resolve_llm_model_falls_back_to_config(monkeypatch):
    monkeypatch.delenv("AIOPS_LLM_MODEL", raising=False)
    assert resolve_llm_model(_config()) == "cfg-model"


@pytest.mark.unit
def test_detector_uses_config_thresholds():
    det = build_detector_agent(
        _config(error_threshold=7, detection_sigma_threshold=2.5)
    )
    assert det.error_threshold == 7
    assert det.sigma_threshold == 2.5


@pytest.mark.unit
def test_diagnoser_gets_model_and_optional_kwargs(monkeypatch):
    monkeypatch.delenv("AIOPS_LLM_MODEL", raising=False)
    reporter = MagicMock()
    diag = build_diagnoser_agent(
        _config(), max_iterations=1, similar_incidents=[{"id": "x"}], reporter=reporter
    )
    assert diag.model == "cfg-model"
    assert diag.max_iterations == 1
    assert diag.similar_incidents == [{"id": "x"}]
    assert diag.reporter is reporter


@pytest.mark.unit
def test_diagnoser_default_max_iterations_preserved():
    from agents.diagnoser import DiagnosisAgent

    assert (
        build_diagnoser_agent(_config()).max_iterations
        == DiagnosisAgent().max_iterations
    )


@pytest.mark.unit
def test_orchestrator_wires_memory_router_client(monkeypatch, tmp_path):
    monkeypatch.delenv("AIOPS_LLM_MODEL", raising=False)
    from rag.memory import IncidentMemoryDB

    memory = IncidentMemoryDB(db_path=str(tmp_path / "mem.db"))
    client = MagicMock(name="llm")
    with patch("adapters.llm.anthropic_client.create_anthropic_llm_client") as factory:
        orch = build_agent_orchestrator(
            _config(), client=client, memory_db=memory, seed_skills=False
        )
    factory.assert_not_called()  # cliente injetado: não cria o Anthropic
    # F6-3: o builder envolve o cliente com o orçamento de chamadas (NFR6)
    from adapters.llm.counting_client import CountingLLMClient

    assert isinstance(orch.client, CountingLLMClient)
    assert orch.client._inner is client
    assert orch.memory_db is memory
    assert orch.model == "cfg-model"
    assert orch.skill_router.db is memory


@pytest.mark.unit
def test_orchestrator_creates_anthropic_client_when_none(monkeypatch, tmp_path):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from rag.memory import IncidentMemoryDB

    memory = IncidentMemoryDB(db_path=str(tmp_path / "mem.db"))
    with patch("adapters.llm.anthropic_client.create_anthropic_llm_client") as factory:
        factory.return_value = MagicMock(name="anthropic")
        orch = build_agent_orchestrator(_config(), memory_db=memory, seed_skills=False)
    factory.assert_called_once_with(api_key="sk-ant-test")
    from adapters.llm.counting_client import CountingLLMClient

    assert isinstance(orch.client, CountingLLMClient)
    assert orch.client._inner is factory.return_value


@pytest.mark.unit
def test_v1_diagnoser_node_detects_with_config_and_reuses_detection(monkeypatch):
    """O node agentic do V1 detecta com config e passa a detecção ao orchestrator."""
    import dataclasses
    from datetime import datetime, timezone

    from blueprint.adapters.pipeline_nodes import make_diagnoser_node
    from domain.incident_state import IncidentState

    cfg = _config(error_threshold=3)
    report = MagicMock()
    report.log_templates = []
    report.metric_anomalies = []
    report.slow_traces = []
    report.affected_services = set()

    now = datetime.now(timezone.utc)
    state = IncidentState(
        incident_id="inc-1",
        correlation_id="inc-1",
        alert=None,
        received_at=now,
        started_at=now,
        no_llm=False,
        no_rag=True,
        aggregated_report={"_report_obj": report},
    )

    fake_orch = MagicMock()
    fake_orch.run.return_value = MagicMock(
        diagnosis={"root_cause": "x"}, mitigation={"action": None}
    )
    with (
        patch(
            "app.builders.build_agent_orchestrator", return_value=fake_orch
        ) as b_orch,
        patch("app.builders.build_detector_agent") as b_det,
    ):
        b_det.return_value.detect.return_value = {
            "is_incident": True,
            "severity": "high",
        }
        new_state = make_diagnoser_node(cfg)(state)

    b_det.assert_called_once_with(cfg)
    b_orch.assert_called_once_with(cfg)
    fake_orch.run.assert_called_once()
    assert fake_orch.run.call_args.kwargs["detection"] == {
        "is_incident": True,
        "severity": "high",
    }
    assert dataclasses.replace(new_state).diagnosis == {"root_cause": "x"}
