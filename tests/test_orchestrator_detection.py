"""F5-2: AgentOrchestrator.run aceita detecção pré-computada.

A CLI V0 detectava com ``config.thresholds`` e o orchestrator detectava DE
NOVO com ``DetectionAgent()`` default — duas detecções, thresholds diferentes.
Agora ``run(report, retriever, detection=...)`` reutiliza o resultado;
``detection=None`` mantém o comportamento antigo.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.orchestrator import AgentOrchestrator


def _orch() -> AgentOrchestrator:
    return AgentOrchestrator(client=None, model="t", memory_db=None, skill_router=None)


def _report() -> MagicMock:
    report = MagicMock()
    report.log_templates = []
    report.affected_services = set()
    return report


@pytest.mark.unit
def test_precomputed_detection_skips_internal_detector():
    with patch("agents.orchestrator.DetectionAgent") as MockDet:
        outcome = _orch().run(
            _report(), retriever=None, detection={"is_incident": False}
        )
    MockDet.assert_not_called()
    assert outcome.diagnosis.get("is_incident") is False


@pytest.mark.unit
def test_without_detection_kwarg_still_detects():
    """Compat: V1 (pipeline_nodes) chama run(report, retriever) sem kwarg."""
    with patch("agents.orchestrator.DetectionAgent") as MockDet:
        MockDet.return_value.detect.return_value = {"is_incident": False}
        outcome = _orch().run(_report(), retriever=None)
    MockDet.assert_called_once()
    assert outcome.diagnosis.get("is_incident") is False


@pytest.mark.unit
def test_heuristic_diagnosis_is_public_and_alias_kept():
    from agents.diagnoser import DiagnosisAgent

    agent = DiagnosisAgent(max_iterations=1)
    assert callable(agent.heuristic_diagnosis)
    # alias legado continua existindo para testes antigos
    assert agent._heuristic_diagnosis.__func__ is not agent.heuristic_diagnosis.__func__
    assert "heuristic_diagnosis" in agent._heuristic_diagnosis.__doc__
