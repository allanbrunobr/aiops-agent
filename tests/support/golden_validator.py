from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ValidationCheck:
    """Check individual dentro da validação."""

    name: str
    passed: bool
    expected: str
    actual: str
    diff: str = ""


@dataclass
class ValidationResult:
    """Resultado da validação contra golden file."""

    scenario_name: str
    passed: bool
    checks: list[ValidationCheck] = field(default_factory=list)

    def summary(self) -> str:
        """Resumo textual do resultado."""
        status = "PASSED" if self.passed else "FAILED"
        failed = [c for c in self.checks if not c.passed]
        return (
            f"[{status}] {self.scenario_name}: "
            f"{len(self.checks)} checks, {len(failed)} failed"
        )


def validate_against_golden(
    actual_output: dict,
    golden_path: str,
) -> ValidationResult:
    """Valida saída do pipeline contra golden file.

    Compara sinais de detecção, serviços afetados e causa raiz.
    Projetado para modo --no-llm (diagnóstico heurístico).
    """
    with open(golden_path, "r", encoding="utf-8") as f:
        golden = json.load(f)

    scenario_name = golden["scenario_name"]
    checks = []

    # Check 1: Incidente declarado
    expected_declared = golden["expected_detection"]["incident_declared"]
    actual_declared = actual_output.get("incident_declared", False)
    checks.append(ValidationCheck(
        name="incident_declared",
        passed=expected_declared == actual_declared,
        expected=str(expected_declared),
        actual=str(actual_declared),
    ))

    # Check 2: Serviços afetados (subset match)
    expected_services = set(golden["expected_affected_services"])
    actual_services = set(actual_output.get("affected_services", []))
    missing = expected_services - actual_services
    checks.append(ValidationCheck(
        name="affected_services",
        passed=expected_services.issubset(actual_services),
        expected=str(sorted(expected_services)),
        actual=str(sorted(actual_services)),
        diff=str(missing) if missing else "",
    ))

    # Check 3: Causa raiz (substring match para modo heurístico)
    expected_contains = golden["expected_diagnosis"]["root_cause_contains"]
    actual_root_cause = actual_output.get("root_cause", "").lower()
    checks.append(ValidationCheck(
        name="root_cause_contains",
        passed=expected_contains.lower() in actual_root_cause,
        expected=f"contains '{expected_contains}'",
        actual=actual_root_cause[:200],
    ))

    all_passed = all(c.passed for c in checks)
    return ValidationResult(
        scenario_name=scenario_name,
        passed=all_passed,
        checks=checks,
    )
