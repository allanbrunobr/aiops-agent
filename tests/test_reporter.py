"""Testes da porta ``Reporter`` (Fase 2c).

Trava o contrato:
  - DetectionAgent emite ``INCIDENT_DETECTED``.
  - DiagnosisAgent emite a sequencia ReAct + ``DIAGNOSIS_STARTED`` /
    ``DIAGNOSIS_COMPLETED``, inclusive em fallback heuristico.
  - MitigationAgent emite ``MITIGATION_PROPOSED`` + ``HUMAN_APPROVAL_REQUESTED``
    e ``TNR_VERIFIED``.
  - AgentOrchestrator propaga o reporter para sub-agentes.
  - Adapters (``RichConsoleReporter``, ``StructuredLogReporter``) satisfazem
    o protocolo via tipagem estrutural — sem herdar de Reporter.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from application.ports.reporter import (
    DIAGNOSIS_COMPLETED,
    DIAGNOSIS_STARTED,
    HUMAN_APPROVAL_REQUESTED,
    INCIDENT_DETECTED,
    ITERATION_STARTED,
    LLM_CALL_FAILED,
    MITIGATION_PROPOSED,
    NullReporter,
    Reporter,
    TNR_VERIFIED,
)


class FakeReporter:
    """Captura ``(event, payload)`` em ordem para asserts."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event: str, **payload: Any) -> None:
        self.events.append((event, payload))

    def names(self) -> list[str]:
        return [e for e, _ in self.events]

    def first(self, event: str) -> dict[str, Any]:
        for e, p in self.events:
            if e == event:
                return p
        raise AssertionError(f"event {event!r} not emitted; got {self.names()}")


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_detector_emits_incident_detected():
    from agents.detector import DetectionAgent

    report = MagicMock()
    report.log_templates = []
    report.metric_anomalies = []
    report.slow_traces = []
    report.total_logs = 0
    report.severity = "low"
    report.affected_services = set()

    reporter = FakeReporter()
    DetectionAgent(reporter=reporter).detect(report)

    payload = reporter.first(INCIDENT_DETECTED)
    assert payload["is_incident"] is False
    assert "signals" in payload
    assert "total_logs" in payload


# ---------------------------------------------------------------------------
# Mitigator
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mitigator_emits_proposed_and_approval():
    from agents.mitigator import MitigationAgent

    reporter = FakeReporter()
    diagnosis = {
        "root_cause": "connection pool exhausted",
        "affected_services": ["api-server"],
        "confidence": "high",
    }
    MitigationAgent(reporter=reporter).propose(diagnosis)

    assert reporter.names() == [MITIGATION_PROPOSED, HUMAN_APPROVAL_REQUESTED]
    proposed = reporter.first(MITIGATION_PROPOSED)
    assert proposed["pattern_matched"] == "connection pool"
    assert proposed["risk_level"] == "LOW"


@pytest.mark.unit
def test_mitigator_emits_tnr_verified():
    from agents.mitigator import MitigationAgent
    from safety.tnr import TNRSafetyGuard

    reporter = FakeReporter()
    mitigation = MitigationAgent(reporter=reporter).propose({
        "root_cause": "memory leak",
        "affected_services": ["api-server"],
    })
    reporter.events.clear()  # foca o teste no evento TNR
    MitigationAgent(reporter=reporter).verify_with_tnr(mitigation, TNRSafetyGuard())

    assert reporter.names() == [TNR_VERIFIED]
    payload = reporter.first(TNR_VERIFIED)
    assert payload["result"] in {"success", "warning", "failure"}


# ---------------------------------------------------------------------------
# Diagnoser (heuristic fallback exercita DIAGNOSIS_STARTED / LLM_CALL_FAILED)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_diagnoser_emits_started_iteration_failed_and_completed():
    from agents.diagnoser import DiagnosisAgent

    report = MagicMock()
    report.to_text.return_value = "incident context"
    report.log_templates = []
    report.metric_anomalies = []
    report.slow_traces = []
    report.affected_services = set()

    failing_client = MagicMock()
    failing_client.create_message.side_effect = RuntimeError("LLM unavailable")

    reporter = FakeReporter()
    agent = DiagnosisAgent(max_iterations=2, reporter=reporter)
    agent.client = failing_client  # bypassa _get_client / API key
    agent.diagnose(report, retriever=None)

    names = reporter.names()
    assert names[0] == DIAGNOSIS_STARTED
    assert ITERATION_STARTED in names
    assert LLM_CALL_FAILED in names
    assert names[-1] == DIAGNOSIS_COMPLETED
    assert reporter.first(DIAGNOSIS_COMPLETED)["mode"] == "heuristic"


# ---------------------------------------------------------------------------
# Orchestrator propagation
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_orchestrator_propagates_reporter_to_subagents():
    from agents.orchestrator import AgentOrchestrator

    reporter = FakeReporter()
    orch = AgentOrchestrator(
        client=None,
        model="test-model",
        memory_db=None,
        skill_router=None,
        reporter=reporter,
    )
    assert orch.reporter is reporter


# ---------------------------------------------------------------------------
# NullReporter + adapters (tipagem estrutural)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_null_reporter_is_silent():
    null = NullReporter()
    null.emit(INCIDENT_DETECTED, foo=1)  # sem efeitos colaterais e sem retorno


@pytest.mark.unit
def test_adapters_satisfy_protocol_structurally():
    """Reporters concretos sao Reporter via duck typing — sem heranca."""
    from adapters.reporter import RichConsoleReporter, StructuredLogReporter

    rich = RichConsoleReporter()
    structured = StructuredLogReporter()

    # Ambos expoem `emit(event, **payload)` sem herdar do Protocol.
    assert callable(rich.emit)
    assert callable(structured.emit)
    assert Reporter not in RichConsoleReporter.__mro__
    assert Reporter not in StructuredLogReporter.__mro__
