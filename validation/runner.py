from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ScenarioResult:
    """Resultado de um cenário de validação."""

    name: str
    passed: bool
    duration_ms: float
    details: str = ""
    expected: str = ""
    actual: str = ""


@dataclass
class ValidationReport:
    """Relatório consolidado de validação."""

    subsystem: str
    scenarios_run: int
    passed: int
    failed: int
    total_duration_ms: float
    results: list[ScenarioResult] = field(default_factory=list)

    def summary(self) -> str:
        """Resumo textual do relatório."""
        status = "PASSED" if self.failed == 0 else "FAILED"
        return (
            f"[{status}] {self.subsystem}: "
            f"{self.scenarios_run} scenarios, "
            f"{self.passed} passed, {self.failed} failed "
            f"({self.total_duration_ms:.0f}ms)"
        )


class ValidationRunner:
    """Executor de cenários de validação com estado isolado.

    Cria instâncias temporárias de KnowledgeStore e InMemoryStore
    para evitar contaminação do estado de produção.
    """

    def __init__(self, subsystem: str = "knowledge") -> None:
        """Inicializa runner com nome do subsistema."""
        self._scenarios: list = []
        self._subsystem = subsystem

    def register(self, scenario: object) -> None:
        """Registra cenário para execução."""
        self._scenarios.append(scenario)

    def run_all(self) -> ValidationReport:
        """Executa todos os cenários registrados com estado isolado."""
        import os
        import tempfile

        results = []
        start = time.monotonic()

        for scenario in self._scenarios:
            with tempfile.TemporaryDirectory() as tmpdir:
                from rag.knowledge_store import KnowledgeStore
                from core.store import InMemoryStore

                isolated_store = InMemoryStore()
                isolated_knowledge = KnowledgeStore(
                    storage_path=os.path.join(tmpdir, "patterns.json"),
                )

                scenario_start = time.monotonic()
                try:
                    result = scenario.execute(
                        knowledge_store=isolated_knowledge,
                        incident_store=isolated_store,
                        temp_dir=tmpdir,
                    )
                    duration = (time.monotonic() - scenario_start) * 1000
                    results.append(ScenarioResult(
                        name=scenario.name,
                        passed=result.passed,
                        duration_ms=duration,
                        details=result.details,
                        expected=result.expected,
                        actual=result.actual,
                    ))
                except Exception as exc:
                    duration = (time.monotonic() - scenario_start) * 1000
                    results.append(ScenarioResult(
                        name=scenario.name,
                        passed=False,
                        duration_ms=duration,
                        details=f"Exception: {exc}",
                    ))
                    logger.warning(
                        "validation.scenario_error",
                        extra={"scenario": scenario.name, "error": str(exc)},
                    )

        total_duration = (time.monotonic() - start) * 1000
        passed = sum(1 for r in results if r.passed)

        return ValidationReport(
            subsystem=self._subsystem,
            scenarios_run=len(results),
            passed=passed,
            failed=len(results) - passed,
            total_duration_ms=total_duration,
            results=results,
        )
