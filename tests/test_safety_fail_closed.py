"""Regressão F2-4: camadas de safety devem ser fail-CLOSED, não fail-open.

Antes do fix:
- exceção no sanitizer/TNR do SafetyOrchestrator → passed=True (skip silencioso);
- exceção de auditoria no pre_validator → engolida sem log;
- _sanitize_result do MCP com exceção → mantinha o texto ORIGINAL (PII vazava).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from domain.incident import MitigationAction


def _benign_action() -> MitigationAction:
    return MitigationAction(
        description="Read-only inspection",
        command="kubectl get pods -n production",
        risk_level="LOW",
        rollback_command=None,
    )


@pytest.mark.unit
def test_sanitizer_exception_blocks():
    from safety.orchestrator import SafetyOrchestrator

    broken = MagicMock()
    broken.sanitize.side_effect = RuntimeError("sanitizer down")
    orch = SafetyOrchestrator(sanitizer=broken)

    result = orch.validate(_benign_action(), diagnosis={})

    assert result.approved is False
    assert "sanitizer" in result.blocked_by
    assert result.layer_details["sanitizer"]["passed"] is False


@pytest.mark.unit
def test_tnr_exception_blocks():
    from safety.orchestrator import SafetyOrchestrator

    broken = MagicMock()
    broken.simulate_check.side_effect = RuntimeError("tnr down")
    orch = SafetyOrchestrator(tnr=broken)

    result = orch.validate(_benign_action(), diagnosis={})

    assert result.approved is False
    assert "tnr" in result.blocked_by
    assert result.layer_details["tnr"]["passed"] is False


@pytest.mark.unit
def test_mcp_sanitize_result_error_redacts_not_leaks():
    from interfaces.mcp.tools import _sanitize_result

    broken = MagicMock()
    broken.sanitize_text.side_effect = RuntimeError("sanitizer down")
    pii = "customer email john.doe@corp.com failed payment"

    out = _sanitize_result({"summary": pii, "diagnosis": pii}, broken)

    assert "john.doe@corp.com" not in out["summary"]
    assert "john.doe@corp.com" not in out["diagnosis"]
    assert out["summary"]
    assert out["diagnosis"]


@pytest.mark.unit
def test_pre_validator_audit_failure_is_logged(monkeypatch):
    """Auditoria que falha (inclusive o log_error) não pode ser silenciosa."""
    import safety.pre_validator as pv
    from safety.pre_validator import PreExecutionValidator

    monkeypatch.setattr(
        "safety.audit.SafetyAuditor.log_decision",
        MagicMock(side_effect=RuntimeError("audit down")),
    )
    monkeypatch.setattr(
        "safety.audit.SafetyAuditor.log_error",
        MagicMock(side_effect=RuntimeError("audit error log down")),
    )
    errors = []
    monkeypatch.setattr(pv.log, "error", lambda *a, **kw: errors.append((a, kw)))

    result = PreExecutionValidator().validate(_benign_action(), diagnosis={})

    # Decisão de validação preservada (auditoria não altera o veredito)
    assert result is not None
    # Mas a falha de auditoria foi logada de forma estruturada
    assert errors, "falha de auditoria engolida sem log"
