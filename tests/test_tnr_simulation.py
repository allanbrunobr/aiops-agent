"""Regressão F2-3: TNR deixa de ser teatro com snapshots hardcoded.

Antes do fix:
- verify_with_tnr fabricava pré (45.0/55.0) e pós (0.1/99.9) → QUALQUER ação
  passava e o replan por regressão nunca disparava;
- SafetyOrchestrator chamava simulate_check com baseline idêntico pré/pós
  → delta 0 → SUCCESS sempre;
- nada distinguia simulação de validação real.

Depois: PRÉ derivado da telemetria do report; PÓS é projeção determinística
declarada (simulate_action_effect); TNRReport.verification_mode identifica
o modo; ações destrutivas produzem REGRESSÃO.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from domain.incident import MitigationAction
from safety.tnr import HealthSnapshot, TNRResult, TNRSafetyGuard


def _make_report(error_rate: float = 45.0, latency: float = 4500.0):
    """Report-like com anomalias/traces reais (shape do AggregatedReport)."""
    return SimpleNamespace(
        metric_anomalies=[
            SimpleNamespace(metric_name="error_rate", value=error_rate),
            SimpleNamespace(metric_name="connection_pool_usage", value=50.0),
        ],
        slow_traces=[SimpleNamespace(total_duration_ms=latency)],
    )


def _make_action(command: str, risk: str = "LOW") -> MitigationAction:
    return MitigationAction(
        description="test action",
        command=command,
        risk_level=risk,
        rollback_command="kubectl rollout undo deployment/payment-svc",
    )


@pytest.mark.unit
def test_pre_snapshot_derived_from_report():
    """PRÉ-ação vem da telemetria do report, não de constantes."""
    from agents.mitigator import MitigationAgent

    mitigation = {"action": _make_action("kubectl rollout undo deployment/payment-svc")}
    report = _make_report(error_rate=45.0, latency=4500.0)

    tnr_report = MitigationAgent().simulate_tnr_verification(
        mitigation, TNRSafetyGuard(), report
    )

    assert tnr_report.pre_snapshot.error_rate == pytest.approx(45.0)
    assert tnr_report.pre_snapshot.latency_p99_ms == pytest.approx(4500.0)
    assert tnr_report.verification_mode == "simulated"


@pytest.mark.unit
def test_destructive_action_simulates_regression():
    """Ação destrutiva → projeção piora → TNR reporta REGRESSION."""
    from agents.mitigator import MitigationAgent

    mitigation = {
        "action": _make_action("kubectl delete pods --all -n production", "HIGH")
    }
    report = _make_report()

    tnr_report = MitigationAgent().simulate_tnr_verification(
        mitigation, TNRSafetyGuard(), report
    )

    assert tnr_report.result == TNRResult.REGRESSION
    assert tnr_report.health_delta < -5.0
    assert tnr_report.post_snapshot.health_score < tnr_report.pre_snapshot.health_score


@pytest.mark.unit
def test_rollback_action_simulates_recovery():
    """Ação de rollback → projeção melhora → SUCCESS com delta positivo."""
    from agents.mitigator import MitigationAgent

    mitigation = {"action": _make_action("kubectl rollout undo deployment/payment-svc")}
    report = _make_report()

    tnr_report = MitigationAgent().simulate_tnr_verification(
        mitigation, TNRSafetyGuard(), report
    )

    assert tnr_report.result == TNRResult.SUCCESS
    assert tnr_report.health_delta > 0.0


@pytest.mark.unit
def test_safety_orchestrator_tnr_snapshots_not_identical():
    """SafetyOrchestrator: pré/pós não podem ser o baseline idêntico (delta 0 sempre)."""
    from safety.orchestrator import SafetyOrchestrator

    action = _make_action("kubectl rollout undo deployment/payment-svc")
    result = SafetyOrchestrator().validate(action, diagnosis={})

    tnr_detail = result.layer_details["tnr"]
    assert not isinstance(tnr_detail, dict), "camada TNR skipou/falhou"
    assert tnr_detail.pre_snapshot != tnr_detail.post_snapshot
    assert tnr_detail.verification_mode == "simulated"


@pytest.mark.unit
def test_safety_orchestrator_destructive_action_blocked_by_tnr():
    """Ação destrutiva que passa pelo pre_validator ainda regride no TNR."""
    from safety.orchestrator import SafetyOrchestrator

    # scale to zero: destrutivo no modelo de simulação
    action = _make_action("kubectl scale deployment/payment-svc --replicas=0", "HIGH")
    result = SafetyOrchestrator().validate(action, diagnosis={})

    tnr_detail = result.layer_details["tnr"]
    assert not isinstance(tnr_detail, dict)
    assert tnr_detail.result == TNRResult.REGRESSION
    assert "tnr" in result.blocked_by
    assert result.approved is False


@pytest.mark.unit
def test_simulate_action_effect_is_deterministic():
    """A projeção é determinística para a mesma ação + snapshot."""
    from safety.tnr import simulate_action_effect

    pre = HealthSnapshot(
        timestamp=datetime(2026, 1, 1),
        error_rate=45.0,
        latency_p99_ms=4500.0,
        availability=55.0,
        connection_pool_active=50,
        connection_pool_max=50,
    )
    action = _make_action("kubectl rollout undo deployment/payment-svc")
    a = simulate_action_effect(action, pre)
    b = simulate_action_effect(action, pre)
    assert a.health_score == b.health_score
    assert a.error_rate < pre.error_rate
