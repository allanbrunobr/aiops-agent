"""Testes para blueprint/engine.py — Story 1.4."""

from __future__ import annotations

import dataclasses
import time
from datetime import datetime, timezone

import pytest

from blueprint.engine import BlueprintEngine, LLMCallLimitExceeded, PipelineAborted, PipelineRunContext
from blueprint.nodes import (
    BlueprintNode,
    NodeType,
    OnFailure,
    PipelineDefinition,
)
from config.loader import AppConfig
from adapters.persistence.in_memory_store import InMemoryStore
from adapters.credentials import EnvCredentialProvider
from core.logging import configure_logging
from domain.alert import NormalizedAlert
from domain.incident_state import IncidentState


@pytest.fixture(autouse=True)
def _setup_logging():
    """Configura logging para todos os testes."""
    configure_logging()


def _make_alert() -> NormalizedAlert:
    """Helper para criar NormalizedAlert de teste."""
    return NormalizedAlert(
        source="pagerduty",
        source_id="PD-12345",
        correlation_id="inc-20260308-a7f3b2c1",
        title="High Error Rate",
        description="payment-svc error rate > 5%",
        severity="critical",
        alert_type="HighErrorRate",
        timestamp=datetime(2026, 3, 8, 14, 0, 0, tzinfo=timezone.utc),
        detection_timestamp=datetime(2026, 3, 8, 14, 0, 5, tzinfo=timezone.utc),
        service="payment-svc",
    )


def _make_state(**overrides) -> IncidentState:
    """Helper para criar IncidentState de teste."""
    alert = _make_alert()
    defaults = {
        "incident_id": "inc-20260308-a7f3b2c1",
        "correlation_id": alert.correlation_id,
        "alert": alert,
        "received_at": datetime(2026, 3, 8, 14, 0, 5, tzinfo=timezone.utc),
        "started_at": alert.timestamp,
    }
    defaults.update(overrides)
    return IncidentState(**defaults)


def _make_engine(**config_overrides) -> BlueprintEngine:
    """Helper para criar BlueprintEngine de teste."""
    config = AppConfig(**config_overrides)
    store = InMemoryStore()
    creds = EnvCredentialProvider()
    return BlueprintEngine(config, store, creds)


def _make_node(node_id: str, fn, **kwargs) -> BlueprintNode:
    """Helper para criar BlueprintNode de teste."""
    return BlueprintNode(
        id=node_id,
        node_type=kwargs.get("node_type", NodeType.DETERMINISTIC),
        execute=fn,
        on_failure=kwargs.get("on_failure", OnFailure.ABORT),
        fallback=kwargs.get("fallback"),
    )


class TestEngineExecution:
    """Testes de execucao do engine."""

    def test_executes_all_nodes_in_order(self):
        """Engine deve executar todos os nodes na ordem definida."""
        engine = _make_engine()
        state = _make_state()
        order = []

        def node_a(s):
            order.append("a")
            return s

        def node_b(s):
            order.append("b")
            return s

        pipeline = PipelineDefinition(
            name="test",
            nodes=[_make_node("a", node_a), _make_node("b", node_b)],
        )

        engine.run(pipeline, state)
        assert order == ["a", "b"]

    def test_passes_state_through_nodes(self):
        """Estado deve fluir entre nodes."""
        engine = _make_engine()
        state = _make_state()

        def set_diagnosis(s):
            return dataclasses.replace(s, diagnosis={"root_cause": "pool exhaustion"})

        pipeline = PipelineDefinition(
            name="test",
            nodes=[_make_node("diagnoser", set_diagnosis)],
        )

        result = engine.run(pipeline, state)
        assert result.diagnosis["root_cause"] == "pool exhaustion"

    def test_persists_final_state(self):
        """Engine deve persistir estado final no store."""
        store = InMemoryStore()
        config = AppConfig()
        creds = EnvCredentialProvider()
        engine = BlueprintEngine(config, store, creds)
        state = _make_state()

        def noop(s):
            return s

        pipeline = PipelineDefinition(
            name="test",
            nodes=[_make_node("noop", noop)],
        )

        engine.run(pipeline, state)
        assert store.get("inc-20260308-a7f3b2c1") is not None


class TestDeterministicFailure:
    """Testes de falha de nodes deterministicos."""

    def test_abort_on_failure(self):
        """Falha em node deterministico com ABORT deve parar o pipeline."""
        engine = _make_engine()
        state = _make_state()

        def failing_node(s):
            raise ValueError("parse error")

        pipeline = PipelineDefinition(
            name="test",
            nodes=[_make_node("parser", failing_node, on_failure=OnFailure.ABORT)],
        )

        with pytest.raises(PipelineAborted, match="parse error"):
            engine.run(pipeline, state)

    def test_skip_on_failure(self):
        """Falha em node com SKIP deve continuar o pipeline."""
        engine = _make_engine()
        state = _make_state()
        executed = []

        def failing_node(s):
            raise ValueError("optional step failed")

        def next_node(s):
            executed.append("next")
            return s

        pipeline = PipelineDefinition(
            name="test",
            nodes=[
                _make_node("optional", failing_node, on_failure=OnFailure.SKIP),
                _make_node("next", next_node),
            ],
        )

        engine.run(pipeline, state)
        assert executed == ["next"]


class TestAgenticFallback:
    """Testes de fallback de nodes agenticos."""

    def test_fallback_on_failure(self):
        """Agentic node deve usar fallback quando execute falha."""
        engine = _make_engine()
        state = _make_state()

        def llm_call(s):
            raise ConnectionError("API unreachable")

        def heuristic(s):
            return dataclasses.replace(s, diagnosis={"root_cause": "heuristic"})

        pipeline = PipelineDefinition(
            name="test",
            nodes=[
                _make_node(
                    "diagnoser",
                    llm_call,
                    node_type=NodeType.AGENTIC,
                    on_failure=OnFailure.FALLBACK,
                    fallback=heuristic,
                ),
            ],
        )

        result = engine.run(pipeline, state)
        assert result.diagnosis["root_cause"] == "heuristic"

    def test_fallback_failure_aborts(self):
        """Se fallback tambem falha, pipeline deve abortar."""
        engine = _make_engine()
        state = _make_state()

        def llm_call(s):
            raise ConnectionError("API unreachable")

        def bad_fallback(s):
            raise RuntimeError("heuristic also failed")

        pipeline = PipelineDefinition(
            name="test",
            nodes=[
                _make_node(
                    "diagnoser",
                    llm_call,
                    node_type=NodeType.AGENTIC,
                    on_failure=OnFailure.FALLBACK,
                    fallback=bad_fallback,
                ),
            ],
        )

        with pytest.raises(PipelineAborted):
            engine.run(pipeline, state)


class TestNoLLMMode:
    """Testes de modo --no-llm."""

    def test_agentic_uses_fallback(self):
        """No modo --no-llm, agentic nodes devem usar fallback direto."""
        engine = _make_engine()
        state = _make_state(no_llm=True)
        llm_called = []

        def llm_call(s):
            llm_called.append(True)
            return s

        def heuristic(s):
            return dataclasses.replace(s, diagnosis={"root_cause": "heuristic_nollm"})

        pipeline = PipelineDefinition(
            name="test",
            nodes=[
                _make_node(
                    "diagnoser",
                    llm_call,
                    node_type=NodeType.AGENTIC,
                    on_failure=OnFailure.FALLBACK,
                    fallback=heuristic,
                ),
            ],
        )

        result = engine.run(pipeline, state)
        assert result.diagnosis["root_cause"] == "heuristic_nollm"
        assert llm_called == []  # LLM nao deve ter sido chamado

    def test_agentic_without_fallback_skips(self):
        """No modo --no-llm, agentic node sem fallback deve ser skipped."""
        engine = _make_engine()
        state = _make_state(no_llm=True)

        def llm_call(s):
            return s

        pipeline = PipelineDefinition(
            name="test",
            nodes=[
                _make_node(
                    "diagnoser",
                    llm_call,
                    node_type=NodeType.AGENTIC,
                    on_failure=OnFailure.ABORT,
                ),
            ],
        )

        # Deve completar sem erro, skipping o node
        engine.run(pipeline, state)


class TestPipelineTimeout:
    """Testes de timeout do pipeline."""

    def test_timeout_aborts(self):
        """Pipeline que excede timeout deve abortar."""
        # Timeout de 0 segundos para forcar abort
        engine = _make_engine(blueprint={"enabled": True, "pipeline_timeout_seconds": 0, "max_llm_calls": 15})
        state = _make_state()

        def slow_node(s):
            time.sleep(0.01)
            return s

        # Primeiro node executa, segundo verifica timeout
        pipeline = PipelineDefinition(
            name="test",
            nodes=[
                _make_node("slow", slow_node),
                _make_node("after", lambda s: s),
            ],
        )

        with pytest.raises(PipelineAborted, match="timeout"):
            engine.run(pipeline, state)


class TestLLMCallLimit:
    """Testes de limite de chamadas LLM."""

    def test_exceeding_limit_raises(self):
        """Exceder limite de chamadas LLM deve gerar excecao."""
        engine = _make_engine(blueprint={"enabled": True, "pipeline_timeout_seconds": 90, "max_llm_calls": 2})
        ctx = PipelineRunContext()

        engine.increment_llm_calls(ctx, 1)
        engine.increment_llm_calls(ctx, 1)
        with pytest.raises(LLMCallLimitExceeded):
            engine.increment_llm_calls(ctx, 1)

    def test_within_limit_ok(self):
        """Chamadas dentro do limite nao devem gerar excecao."""
        engine = _make_engine(blueprint={"enabled": True, "pipeline_timeout_seconds": 90, "max_llm_calls": 15})
        ctx = PipelineRunContext()

        for _ in range(15):
            engine.increment_llm_calls(ctx, 1)
        # Nao deve ter gerado excecao


class TestPipelineIsolation:
    """Testes de isolamento entre runs."""

    def test_two_runs_independent_state(self):
        """Dois runs devem ter IncidentState independentes."""
        engine = _make_engine()
        state1 = _make_state(incident_id="inc-001")
        state2 = _make_state(incident_id="inc-002")

        results = []

        def capture(s):
            results.append(s.incident_id)
            return s

        pipeline = PipelineDefinition(
            name="test",
            nodes=[_make_node("capture", capture)],
        )

        engine.run(pipeline, state1)
        engine.run(pipeline, state2)

        assert results == ["inc-001", "inc-002"]
