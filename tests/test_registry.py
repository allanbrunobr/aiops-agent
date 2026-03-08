"""
Testes unitários para o scenario registry e abstração de cenários.

Verifica registro, lookup, determinismo por seed e backward-compatibility
da função create_incident().
"""
from __future__ import annotations

import datetime

import pytest

# Importar sample_incident primeiro — dispara register_scenario() em module-level.
from data.sample_incident import create_incident
from data.models import LogEntry, MetricPoint, Span, SimulatedIncident
from data.registry import get_scenario, list_scenarios


@pytest.mark.unit
class TestScenarioRegistry:
    """Testes do registry de cenários (AC: #1, #2)."""

    def test_payment_pool_registered(self) -> None:
        """Cenário 'payment-pool-exhaustion' deve estar registrado."""
        scenarios = list_scenarios()
        assert "payment-pool-exhaustion" in scenarios

    def test_get_unknown_scenario_raises_keyerror(self) -> None:
        """get_scenario() com nome desconhecido deve levantar KeyError com mensagem útil."""
        with pytest.raises(KeyError, match="não encontrado"):
            get_scenario("nonexistent-scenario")


@pytest.mark.unit
class TestScenarioDeterminism:
    """Testes de determinismo por seed (AC: #3)."""

    def test_scenario_deterministic_seed42(self) -> None:
        """Duas chamadas com seed=42 devem produzir output idêntico."""
        fn = get_scenario("payment-pool-exhaustion")
        inc1 = fn(seed=42)
        inc2 = fn(seed=42)

        assert len(inc1.logs) == len(inc2.logs)
        assert len(inc1.metrics) == len(inc2.metrics)
        assert len(inc1.traces) == len(inc2.traces)
        assert inc1.actual_root_cause == inc2.actual_root_cause

        # Verifica conteúdo dos logs (não apenas contagem).
        for log1, log2 in zip(inc1.logs, inc2.logs):
            assert str(log1) == str(log2)

        # Verifica conteúdo das métricas (valores numéricos determinísticos).
        for m1, m2 in zip(inc1.metrics, inc2.metrics):
            assert m1.service == m2.service
            assert m1.metric_name == m2.metric_name
            assert m1.value == m2.value

        # Verifica conteúdo dos traces (spans determinísticos).
        for t1, t2 in zip(inc1.traces, inc2.traces):
            assert t1.trace_id == t2.trace_id
            assert t1.span_id == t2.span_id
            assert t1.service == t2.service
            assert t1.duration_ms == t2.duration_ms
            assert t1.status == t2.status

    def test_scenario_different_seed_different_output(self) -> None:
        """Seeds diferentes (42 vs 99) devem produzir mensagens de log diferentes."""
        fn = get_scenario("payment-pool-exhaustion")
        inc42 = fn(seed=42)
        inc99 = fn(seed=99)

        # Logs são gerados com random — seeds diferentes produzem mensagens diferentes.
        logs42_str = [str(log) for log in inc42.logs]
        logs99_str = [str(log) for log in inc99.logs]
        assert logs42_str != logs99_str


@pytest.mark.unit
class TestMemoryLeakScenario:
    """Testes do cenário memory-leak (Story 2.2 — AC: #1, #2)."""

    def test_memory_leak_registered(self) -> None:
        """Cenário 'memory-leak' deve estar registrado no registry."""
        scenarios = list_scenarios()
        assert "memory-leak" in scenarios

    def test_memory_leak_deterministic_seed42(self) -> None:
        """Duas chamadas com seed=42 devem produzir output idêntico."""
        fn = get_scenario("memory-leak")
        inc1 = fn(seed=42)
        inc2 = fn(seed=42)

        assert len(inc1.logs) == len(inc2.logs)
        assert len(inc1.metrics) == len(inc2.metrics)
        assert len(inc1.traces) == len(inc2.traces)
        assert inc1.actual_root_cause == inc2.actual_root_cause
        assert inc1.actual_fix == inc2.actual_fix

        # Verifica conteúdo dos logs (não apenas contagem).
        for log1, log2 in zip(inc1.logs, inc2.logs):
            assert str(log1) == str(log2)

        # Verifica conteúdo das métricas (valores numéricos determinísticos).
        for m1, m2 in zip(inc1.metrics, inc2.metrics):
            assert m1.service == m2.service
            assert m1.metric_name == m2.metric_name
            assert m1.value == m2.value

        # Verifica conteúdo dos traces (spans determinísticos).
        for t1, t2 in zip(inc1.traces, inc2.traces):
            assert t1.trace_id == t2.trace_id
            assert t1.span_id == t2.span_id
            assert t1.service == t2.service
            assert t1.duration_ms == t2.duration_ms
            assert t1.status == t2.status

    def test_memory_leak_has_correct_services(self) -> None:
        """Cenário memory-leak deve ter telemetria de pelo menos 3 serviços distintos."""
        fn = get_scenario("memory-leak")
        inc = fn(seed=42)
        services = set(log.service for log in inc.logs)
        assert len(services) >= 3, f"Esperado ≥3 serviços, encontrado {len(services)}: {services}"

    def test_memory_leak_root_cause_contains_memory(self) -> None:
        """actual_root_cause deve conter 'memory' (case-insensitive)."""
        fn = get_scenario("memory-leak")
        inc = fn(seed=42)
        assert "memory" in inc.actual_root_cause.lower(), (
            f"actual_root_cause não contém 'memory': {inc.actual_root_cause}"
        )

    def test_memory_leak_contract(self) -> None:
        """SimulatedIncident do memory-leak deve ter todos os 9 campos com tipos corretos."""
        fn = get_scenario("memory-leak")
        inc = fn(seed=42)

        expected_fields = {
            "logs", "metrics", "traces",
            "deploy_time", "spike_start", "detection_time", "fix_time",
            "actual_root_cause", "actual_fix",
        }
        actual_fields = set(inc.__dataclass_fields__.keys())
        assert actual_fields == expected_fields

        # Verifica tipos dos campos.
        assert isinstance(inc, SimulatedIncident)
        assert isinstance(inc.logs, list) and len(inc.logs) > 0
        assert isinstance(inc.metrics, list) and len(inc.metrics) > 0
        assert isinstance(inc.traces, list) and len(inc.traces) > 0
        assert isinstance(inc.logs[0], LogEntry)
        assert isinstance(inc.metrics[0], MetricPoint)
        assert isinstance(inc.traces[0], Span)
        assert isinstance(inc.deploy_time, datetime.datetime)
        assert isinstance(inc.spike_start, datetime.datetime)
        assert isinstance(inc.detection_time, datetime.datetime)
        assert isinstance(inc.fix_time, datetime.datetime)
        assert isinstance(inc.actual_root_cause, str) and len(inc.actual_root_cause) > 0
        assert isinstance(inc.actual_fix, str) and len(inc.actual_fix) > 0


@pytest.mark.unit
class TestBackwardCompatibility:
    """Testes de backward-compatibility (AC: #4, #5)."""

    def test_create_incident_backward_compatible(self) -> None:
        """create_incident() deve retornar SimulatedIncident válido com tipos esperados."""
        incident = create_incident()

        assert isinstance(incident, SimulatedIncident)
        assert isinstance(incident.logs, list)
        assert isinstance(incident.metrics, list)
        assert isinstance(incident.traces, list)
        assert len(incident.logs) > 0
        assert len(incident.metrics) > 0
        assert len(incident.traces) > 0

        # Verifica tipos dos elementos.
        assert isinstance(incident.logs[0], LogEntry)
        assert isinstance(incident.metrics[0], MetricPoint)
        assert isinstance(incident.traces[0], Span)

    def test_simulated_incident_contract(self) -> None:
        """SimulatedIncident deve ter exatamente os 9 campos do contrato."""
        expected_fields = {
            "logs", "metrics", "traces",
            "deploy_time", "spike_start", "detection_time", "fix_time",
            "actual_root_cause", "actual_fix",
        }
        incident = create_incident()

        # Verifica que todos os campos existem e têm valores não-None.
        actual_fields = set(incident.__dataclass_fields__.keys())
        assert actual_fields == expected_fields

        # Verifica tipos dos campos de timestamp.
        import datetime
        assert isinstance(incident.deploy_time, datetime.datetime)
        assert isinstance(incident.spike_start, datetime.datetime)
        assert isinstance(incident.detection_time, datetime.datetime)
        assert isinstance(incident.fix_time, datetime.datetime)

        # Verifica campos de texto.
        assert isinstance(incident.actual_root_cause, str)
        assert len(incident.actual_root_cause) > 0
        assert isinstance(incident.actual_fix, str)
        assert len(incident.actual_fix) > 0
