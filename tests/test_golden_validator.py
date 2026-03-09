from __future__ import annotations

"""
Testes para Story 4.4: New Diagnostic Scenario Template.
Cobre utils/golden_validator.py.
"""

import json
import pytest

from utils.golden_validator import validate_against_golden, ValidationResult


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def golden_file(tmp_path):
    """Cria golden file temporário para testes."""
    golden = {
        "scenario_name": "test-scenario",
        "scenario_seed": 42,
        "expected_detection": {
            "incident_declared": True,
            "correlated_signals": {
                "error_logs": True,
                "metric_anomalies": True,
                "slow_traces": True,
            },
            "min_signals_required": 2,
        },
        "expected_diagnosis": {
            "affected_services": ["payment-svc"],
            "root_cause_contains": "connection pool",
            "severity": "high",
        },
        "expected_affected_services": ["payment-svc"],
        "validation_mode": "no-llm",
    }
    path = tmp_path / "test-scenario.json"
    path.write_text(json.dumps(golden))
    return str(path)


# ── validate_against_golden ───────────────────────────────────────────────────

def test_validate_all_pass(golden_file):
    """Saída que atende todos os critérios deve passar."""
    actual = {
        "incident_declared": True,
        "affected_services": ["payment-svc", "api-svc"],
        "root_cause": "Connection pool exhausted after config deploy",
    }
    result = validate_against_golden(actual, golden_file)

    assert result.passed is True
    assert all(c.passed for c in result.checks)
    assert result.scenario_name == "test-scenario"


def test_validate_incident_not_declared_fails(golden_file):
    """Se incident_declared=False mas esperado True, deve falhar."""
    actual = {
        "incident_declared": False,
        "affected_services": ["payment-svc"],
        "root_cause": "connection pool exhausted",
    }
    result = validate_against_golden(actual, golden_file)

    assert result.passed is False
    failed = [c for c in result.checks if not c.passed]
    assert any(c.name == "incident_declared" for c in failed)


def test_validate_affected_services_subset_match(golden_file):
    """Affected services deve usar subset match (expected ⊆ actual)."""
    # Apenas payment-svc é esperado; actual tem mais serviços — deve passar
    actual = {
        "incident_declared": True,
        "affected_services": ["payment-svc", "cache-svc", "worker"],
        "root_cause": "connection pool exhausted",
    }
    result = validate_against_golden(actual, golden_file)
    assert result.passed is True


def test_validate_missing_service_fails(golden_file):
    """Se serviço esperado não está em affected_services, deve falhar."""
    actual = {
        "incident_declared": True,
        "affected_services": ["api-svc"],  # payment-svc ausente
        "root_cause": "connection pool exhausted",
    }
    result = validate_against_golden(actual, golden_file)

    assert result.passed is False
    failed = [c for c in result.checks if not c.passed]
    assert any(c.name == "affected_services" for c in failed)


def test_validate_root_cause_case_insensitive(golden_file):
    """root_cause_contains deve ser case-insensitive."""
    actual = {
        "incident_declared": True,
        "affected_services": ["payment-svc"],
        "root_cause": "CONNECTION POOL exhaustion detected",  # uppercase
    }
    result = validate_against_golden(actual, golden_file)
    assert result.passed is True


def test_validate_root_cause_missing_keyword_fails(golden_file):
    """Se causa raiz não contém keyword esperada, deve falhar."""
    actual = {
        "incident_declared": True,
        "affected_services": ["payment-svc"],
        "root_cause": "CPU spike due to inefficient query",
    }
    result = validate_against_golden(actual, golden_file)

    assert result.passed is False
    failed = [c for c in result.checks if not c.passed]
    assert any(c.name == "root_cause_contains" for c in failed)


def test_validate_summary_contains_status():
    """ValidationResult.summary() deve indicar PASSED ou FAILED."""
    result = ValidationResult(
        scenario_name="test",
        passed=True,
        checks=[],
    )
    assert "PASSED" in result.summary()

    result_failed = ValidationResult(
        scenario_name="test",
        passed=False,
        checks=[],
    )
    assert "FAILED" in result_failed.summary()


def test_validate_partial_actual_missing_fields(golden_file):
    """actual_output com campos ausentes deve usar defaults graciosamente."""
    actual: dict = {}  # completamente vazio

    result = validate_against_golden(actual, golden_file)

    # Deve falhar mas não gerar exception
    assert result.passed is False
    assert len(result.checks) == 3  # 3 checks mesmo com dados ausentes
