from __future__ import annotations

"""
Testes para Story 3.4: Safety Audit Trail.
Cobre SafetyAuditEvent, SafetyAuditor e instrumentação das 4 camadas de segurança.
"""

import pytest
import structlog.testing

from safety.audit import SafetyAuditEvent, SafetyAuditor


# ── SafetyAuditEvent ──────────────────────────────────────────────────────────

def test_audit_event_is_frozen():
    """SafetyAuditEvent deve ser imutável (frozen dataclass)."""
    event = SafetyAuditEvent(
        layer_name="sanitizer",
        correlation_id="corr-001",
        verdict="pass",
        rationale="No threats",
        input_summary="50 logs",
        output_summary="50 logs clean",
    )
    with pytest.raises((AttributeError, TypeError)):
        event.verdict = "block"  # type: ignore[misc]


def test_audit_event_defaults():
    """SafetyAuditEvent deve ter timestamp e duration_ms com defaults."""
    event = SafetyAuditEvent(
        layer_name="pre_validator",
        correlation_id="c1",
        verdict="block",
        rationale="High blast radius",
        input_summary="kubectl delete",
        output_summary="blocked",
    )
    assert event.triggered_rule is None
    assert event.duration_ms == 0
    assert event.metadata == {}
    assert event.timestamp is not None


# ── SafetyAuditor.log_decision ─────────────────────────────────────────────────

def test_log_decision_pass_emits_info():
    """Veredito 'pass' deve emitir log de nível info."""
    event = SafetyAuditEvent(
        layer_name="sanitizer",
        correlation_id="corr-001",
        verdict="pass",
        rationale="Clean",
        input_summary="100 events",
        output_summary="100 clean",
    )
    with structlog.testing.capture_logs() as logs:
        SafetyAuditor.log_decision(event)

    assert len(logs) == 1
    log = logs[0]
    assert log["log_level"] == "info"
    assert log["event"] == "safety.sanitizer"
    assert log["verdict"] == "pass"
    assert log["correlation_id"] == "corr-001"


def test_log_decision_block_emits_warning():
    """Veredito 'block' deve emitir log de nível warning."""
    event = SafetyAuditEvent(
        layer_name="pre_validator",
        correlation_id="corr-002",
        verdict="block",
        rationale="Blast radius too high",
        input_summary="kubectl delete ns prod",
        output_summary="blocked",
        triggered_rule="max_blast_radius",
    )
    with structlog.testing.capture_logs() as logs:
        SafetyAuditor.log_decision(event)

    assert len(logs) == 1
    log = logs[0]
    assert log["log_level"] == "warning"
    assert log["event"] == "safety.pre_validator"
    assert log["verdict"] == "block"
    assert log["triggered_rule"] == "max_blast_radius"


def test_log_decision_rollback_emits_warning():
    """Veredito 'rollback' deve emitir log de nível warning."""
    event = SafetyAuditEvent(
        layer_name="tnr",
        correlation_id="corr-003",
        verdict="rollback",
        rationale="Health dropped below threshold",
        input_summary="pre=0.9 post=0.4",
        output_summary="rollback triggered",
    )
    with structlog.testing.capture_logs() as logs:
        SafetyAuditor.log_decision(event)

    log = logs[0]
    assert log["log_level"] == "warning"
    assert log["event"] == "safety.tnr"


def test_log_error_emits_error_level():
    """log_error deve emitir evento com verdict='error' em nível error."""
    try:
        raise ValueError("Something broke")
    except ValueError as exc:
        with structlog.testing.capture_logs() as logs:
            SafetyAuditor.log_error("version_validator", "corr-004", exc)

    assert len(logs) == 1
    log = logs[0]
    assert log["log_level"] == "error"
    assert log["event"] == "safety.version_validator"
    assert log["verdict"] == "error"


def test_log_decision_includes_metadata():
    """Metadata adicional deve ser incluída no evento de log."""
    event = SafetyAuditEvent(
        layer_name="tnr",
        correlation_id="corr-005",
        verdict="rollback",
        rationale="Health regression",
        input_summary="pre_score=0.9",
        output_summary="post_score=0.3",
        metadata={"pre_score": 0.9, "post_score": 0.3, "delta": -0.6},
    )
    with structlog.testing.capture_logs() as logs:
        SafetyAuditor.log_decision(event)

    log = logs[0]
    assert log["pre_score"] == 0.9
    assert log["post_score"] == 0.3
    assert log["delta"] == -0.6


# ── Instrumentação das camadas de segurança ───────────────────────────────────

def test_sanitizer_emits_audit_log_on_clean_input():
    """TelemetrySanitizer deve emitir evento safety.sanitizer ao processar logs limpos."""
    from pipeline.telemetry_sanitizer import TelemetrySanitizer

    sanitizer = TelemetrySanitizer(strict_mode=False)

    from data.models import LogEntry
    import datetime

    logs = [
        LogEntry(
            timestamp=datetime.datetime(2026, 3, 4, 14, 0, 0),
            level="INFO",
            service="api-svc",
            message="Request OK",
        )
    ]

    with structlog.testing.capture_logs() as captured:
        sanitizer.sanitize_all(logs, [], [], correlation_id="corr-san-001")

    audit_events = [e for e in captured if "safety.sanitizer" in e.get("event", "")]
    assert len(audit_events) == 1
    assert audit_events[0]["correlation_id"] == "corr-san-001"
    assert audit_events[0]["verdict"] == "pass"


def test_pre_validator_emits_audit_log():
    """PreExecutionValidator deve emitir evento safety.pre_validator."""
    from safety.pre_validator import PreExecutionValidator
    from safety.tnr import MitigationAction

    validator = PreExecutionValidator(
        auto_approve_low_risk=True,
        max_blast_radius=3,
        require_rollback_plan=True,
    )
    action = MitigationAction(
        description="Restart deployment",
        command="kubectl rollout restart deployment/api-svc",
        risk_level="LOW",
        rollback_command="kubectl rollout undo deployment/api-svc",
    )

    with structlog.testing.capture_logs() as captured:
        validator.validate(action, {}, correlation_id="corr-pv-001")

    audit_events = [e for e in captured if "safety.pre_validator" in e.get("event", "")]
    assert len(audit_events) == 1
    assert audit_events[0]["correlation_id"] == "corr-pv-001"
