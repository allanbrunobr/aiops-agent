from __future__ import annotations

"""
Testes para Story 4.6: Bundled Test Scenarios.
Cobre ValidationRunner, ScenarioResult, ValidationReport.
"""

import pytest

from validation.runner import ValidationRunner, ScenarioResult, ValidationReport


# ── helpers ───────────────────────────────────────────────────────────────────

class _PassingScenario:
    """Cenário de validação que sempre passa."""

    name = "passing_scenario"

    def execute(self, knowledge_store, incident_store, temp_dir):
        """Executa cenário e retorna resultado aprovado."""
        return ScenarioResult(
            name=self.name,
            passed=True,
            duration_ms=10.0,
            details="All checks passed",
            expected="pattern in results",
            actual="pattern in results",
        )


class _FailingScenario:
    """Cenário de validação que sempre falha."""

    name = "failing_scenario"

    def execute(self, knowledge_store, incident_store, temp_dir):
        """Executa cenário e retorna resultado com falha."""
        return ScenarioResult(
            name=self.name,
            passed=False,
            duration_ms=5.0,
            details="Pattern not found",
            expected="pat-001 in results",
            actual="[]",
        )


class _ErrorScenario:
    """Cenário de validação que lança exception."""

    name = "error_scenario"

    def execute(self, knowledge_store, incident_store, temp_dir):
        """Lança exception para testar error handling."""
        raise RuntimeError("Unexpected failure in scenario")


# ── ValidationRunner ──────────────────────────────────────────────────────────

def test_runner_no_scenarios():
    """Runner sem cenários deve retornar relatório vazio."""
    runner = ValidationRunner(subsystem="test")
    report = runner.run_all()

    assert report.scenarios_run == 0
    assert report.passed == 0
    assert report.failed == 0


def test_runner_single_passing_scenario():
    """Runner com 1 cenário que passa deve retornar passed=1."""
    runner = ValidationRunner(subsystem="knowledge")
    runner.register(_PassingScenario())

    report = runner.run_all()

    assert report.scenarios_run == 1
    assert report.passed == 1
    assert report.failed == 0
    assert report.subsystem == "knowledge"


def test_runner_single_failing_scenario():
    """Runner com 1 cenário que falha deve retornar failed=1."""
    runner = ValidationRunner(subsystem="knowledge")
    runner.register(_FailingScenario())

    report = runner.run_all()

    assert report.scenarios_run == 1
    assert report.passed == 0
    assert report.failed == 1


def test_runner_mixed_scenarios():
    """Runner com cenários mistos deve contabilizar corretamente."""
    runner = ValidationRunner(subsystem="knowledge")
    runner.register(_PassingScenario())
    runner.register(_FailingScenario())

    report = runner.run_all()

    assert report.scenarios_run == 2
    assert report.passed == 1
    assert report.failed == 1


def test_runner_error_scenario_counted_as_failure():
    """Cenário que lança exception deve ser contabilizado como failure."""
    runner = ValidationRunner(subsystem="knowledge")
    runner.register(_ErrorScenario())

    report = runner.run_all()

    assert report.scenarios_run == 1
    assert report.failed == 1
    assert report.passed == 0

    result = report.results[0]
    assert "Exception" in result.details


def test_runner_uses_isolated_state():
    """Runner deve usar estado isolado (KnowledgeStore temporário) por cenário."""
    seen_stores = []

    class _StoreCapture:
        name = "capture"

        def execute(self, knowledge_store, incident_store, temp_dir):
            seen_stores.append(id(knowledge_store))
            return ScenarioResult(name=self.name, passed=True, duration_ms=1.0)

    runner = ValidationRunner(subsystem="knowledge")
    runner.register(_StoreCapture())
    runner.register(_StoreCapture())

    runner.run_all()

    # Cada execução deve receber store diferente (IDs diferentes)
    assert len(seen_stores) == 2
    assert seen_stores[0] != seen_stores[1]


def test_runner_report_summary_pass():
    """Summary deve indicar PASSED quando todos os cenários passam."""
    runner = ValidationRunner(subsystem="knowledge")
    runner.register(_PassingScenario())

    report = runner.run_all()

    assert "PASSED" in report.summary()


def test_runner_report_summary_fail():
    """Summary deve indicar FAILED quando algum cenário falha."""
    runner = ValidationRunner(subsystem="knowledge")
    runner.register(_FailingScenario())

    report = runner.run_all()

    assert "FAILED" in report.summary()


def test_runner_total_duration_ms_positive():
    """total_duration_ms deve ser positivo após execução."""
    runner = ValidationRunner(subsystem="knowledge")
    runner.register(_PassingScenario())

    report = runner.run_all()

    assert report.total_duration_ms > 0
