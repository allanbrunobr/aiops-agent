"""Testes para Story 2.3: PagerDuty, Grafana e Databricks Normalizers."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

import pytest

from interfaces.http.webhook.normalizers.pagerduty import PagerDutyNormalizer
from interfaces.http.webhook.normalizers.grafana import GrafanaNormalizer
from interfaces.http.webhook.normalizers.databricks import DatabricksNormalizer


class MockCredentials:
    """Mock de CredentialProvider para testes."""

    def __init__(self, secrets: dict[str, str]):
        self._secrets = secrets

    def get(self, key: str) -> str | None:
        return self._secrets.get(key)


# ---------------------------------------------------------------------------
# PagerDuty
# ---------------------------------------------------------------------------

def test_pagerduty_hmac_valid():
    """AC1: PagerDuty HMAC valido e aceito."""
    secret = "test-secret-key"
    creds = MockCredentials({"PAGERDUTY_WEBHOOK_SECRET": secret})
    normalizer = PagerDutyNormalizer()

    body = json.dumps({"event": {"data": {"id": "P1"}}}).encode()
    signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"x-pagerduty-signature": f"v1={signature}"}

    assert normalizer.validate_auth(headers, body, creds) is True


def test_pagerduty_hmac_invalid():
    """AC1: PagerDuty HMAC invalido e rejeitado."""
    creds = MockCredentials({"PAGERDUTY_WEBHOOK_SECRET": "real-secret"})
    normalizer = PagerDutyNormalizer()

    body = b'{"event": {}}'
    headers = {"x-pagerduty-signature": "v1=invalid_hex"}

    assert normalizer.validate_auth(headers, body, creds) is False


def test_pagerduty_hmac_missing_header():
    """AC5: requisicao sem header de assinatura e rejeitada."""
    creds = MockCredentials({"PAGERDUTY_WEBHOOK_SECRET": "secret"})
    normalizer = PagerDutyNormalizer()

    assert normalizer.validate_auth({}, b"body", creds) is False


def test_pagerduty_hmac_missing_secret():
    """AC5: validacao falha quando secret nao esta configurado."""
    creds = MockCredentials({})
    normalizer = PagerDutyNormalizer()
    headers = {"x-pagerduty-signature": "v1=abc123"}

    assert normalizer.validate_auth(headers, b"body", creds) is False


def test_pagerduty_normalize():
    """AC1: PagerDuty normalize produz NormalizedAlert valido."""
    normalizer = PagerDutyNormalizer()
    payload = {
        "event": {
            "event_type": "incident.triggered",
            "occurred_at": "2026-03-08T10:00:00Z",
            "data": {
                "id": "P123ABC",
                "title": "High CPU on payment-svc",
                "description": "CPU usage above 90%",
                "urgency": "high",
                "service": {"summary": "payment-svc"},
            },
        }
    }

    alert = normalizer.normalize(payload)
    assert alert.source == "pagerduty"
    assert alert.severity == "critical"  # "high" -> "critical"
    assert alert.title == "High CPU on payment-svc"
    assert alert.service == "payment-svc"
    assert alert.correlation_id.startswith("inc-")


def test_pagerduty_normalize_missing_data_raises():
    """AC: payload sem 'event.data' levanta ValueError."""
    normalizer = PagerDutyNormalizer()
    with pytest.raises(ValueError, match="missing 'event.data'"):
        normalizer.normalize({"event": {}})


def test_pagerduty_severity_mapping():
    """AC: severidades sao mapeadas corretamente."""
    normalizer = PagerDutyNormalizer()
    for urgency, expected in [("critical", "critical"), ("high", "critical"),
                               ("low", "warning"), ("info", "info")]:
        payload = {
            "event": {
                "event_type": "incident.triggered",
                "data": {"id": "X", "title": "T", "urgency": urgency},
            }
        }
        alert = normalizer.normalize(payload)
        assert alert.severity == expected, f"urgency={urgency}"


# ---------------------------------------------------------------------------
# Grafana
# ---------------------------------------------------------------------------

def test_grafana_token_valid():
    """AC2: Grafana Bearer token valido e aceito."""
    creds = MockCredentials({"GRAFANA_WEBHOOK_TOKEN": "secret-token"})
    normalizer = GrafanaNormalizer()
    headers = {"authorization": "Bearer secret-token"}

    assert normalizer.validate_auth(headers, b"body", creds) is True


def test_grafana_token_invalid():
    """AC2: Grafana token invalido e rejeitado."""
    creds = MockCredentials({"GRAFANA_WEBHOOK_TOKEN": "correct-token"})
    normalizer = GrafanaNormalizer()
    headers = {"authorization": "Bearer wrong-token"}

    assert normalizer.validate_auth(headers, b"body", creds) is False


def test_grafana_token_missing():
    """AC5: requisicao Grafana sem Authorization e rejeitada."""
    creds = MockCredentials({"GRAFANA_WEBHOOK_TOKEN": "token"})
    normalizer = GrafanaNormalizer()

    assert normalizer.validate_auth({}, b"body", creds) is False


def test_grafana_normalize():
    """AC2: Grafana normalize produz NormalizedAlert valido."""
    normalizer = GrafanaNormalizer()
    payload = {
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {"alertname": "HighCPU", "severity": "critical", "service": "api"},
                "annotations": {"description": "CPU usage > 90%"},
                "startsAt": "2026-03-08T10:00:00Z",
                "fingerprint": "abc123def456",
            }
        ],
        "commonLabels": {"alertname": "HighCPU"},
    }

    alert = normalizer.normalize(payload)
    assert alert.source == "grafana"
    assert alert.severity == "critical"
    assert alert.title == "HighCPU"
    assert alert.service == "api"
    assert alert.correlation_id.startswith("inc-")


def test_grafana_normalize_empty_alerts_raises():
    """AC: payload com alerts vazio levanta ValueError."""
    normalizer = GrafanaNormalizer()
    with pytest.raises(ValueError, match="missing 'alerts'"):
        normalizer.normalize({"alerts": []})


# ---------------------------------------------------------------------------
# Databricks
# ---------------------------------------------------------------------------

def test_databricks_token_valid():
    """AC3: Databricks token valido e aceito."""
    creds = MockCredentials({"DATABRICKS_WEBHOOK_TOKEN": "db-token"})
    normalizer = DatabricksNormalizer()
    headers = {"authorization": "Bearer db-token"}

    assert normalizer.validate_auth(headers, b"body", creds) is True


def test_databricks_token_invalid():
    """AC3: Databricks token invalido e rejeitado."""
    creds = MockCredentials({"DATABRICKS_WEBHOOK_TOKEN": "correct"})
    normalizer = DatabricksNormalizer()
    headers = {"authorization": "Bearer wrong"}

    assert normalizer.validate_auth(headers, b"body", creds) is False


def test_databricks_normalize_sql_alert():
    """AC3: Databricks SQL alert produz NormalizedAlert valido."""
    normalizer = DatabricksNormalizer()
    payload = {
        "alert_id": "alert-123",
        "alert_name": "High Error Rate",
        "severity": "ERROR",
        "message": "Error rate above threshold",
        "state": "TRIGGERED",
        "triggered_at": "2026-03-08T10:00:00Z",
    }

    alert = normalizer.normalize(payload)
    assert alert.source == "databricks"
    assert alert.severity == "error"
    assert alert.alert_type == "databricks.sql_alert"
    assert alert.correlation_id.startswith("inc-")


def test_databricks_normalize_job_notification():
    """AC3: Databricks job notification produz NormalizedAlert valido."""
    normalizer = DatabricksNormalizer()
    payload = {
        "job_id": 123,
        "run_id": 456,
        "run_name": "ETL Pipeline",
        "result_state": "FAILED",
        "state_message": "Task failed with exit code 1",
        "start_time": 1709913781000,
    }

    alert = normalizer.normalize(payload)
    assert alert.source == "databricks"
    assert alert.severity == "critical"
    assert alert.alert_type == "databricks.job_notification"
    assert "456" in alert.source_id


def test_databricks_normalize_unknown_payload_raises():
    """AC: payload sem alert_id ou job_id levanta ValueError."""
    normalizer = DatabricksNormalizer()
    with pytest.raises(ValueError, match="not recognized"):
        normalizer.normalize({"unknown_key": "value"})


# ---------------------------------------------------------------------------
# Esquema unificado (AC4)
# ---------------------------------------------------------------------------

def test_all_normalizers_produce_identical_schema():
    """AC4: todos os normalizers produzem NormalizedAlert com campos identicos."""
    from domain.alert import NormalizedAlert

    pd_normalizer = PagerDutyNormalizer()
    gf_normalizer = GrafanaNormalizer()
    db_normalizer = DatabricksNormalizer()

    pd_alert = pd_normalizer.normalize({
        "event": {
            "event_type": "incident.triggered",
            "data": {"id": "P1", "title": "PD Alert", "urgency": "high"},
        }
    })

    gf_alert = gf_normalizer.normalize({
        "alerts": [{
            "status": "firing",
            "labels": {"alertname": "GF Alert"},
            "annotations": {},
            "fingerprint": "fp1",
        }]
    })

    db_alert = db_normalizer.normalize({
        "alert_id": "db-1",
        "alert_name": "DB Alert",
        "severity": "ERROR",
        "message": "test",
    })

    # Todos devem ser instancias de NormalizedAlert com mesmos campos
    for alert in [pd_alert, gf_alert, db_alert]:
        assert isinstance(alert, NormalizedAlert)
        assert hasattr(alert, "source")
        assert hasattr(alert, "severity")
        assert hasattr(alert, "title")
        assert hasattr(alert, "correlation_id")
        assert hasattr(alert, "timestamp")
        assert hasattr(alert, "detection_timestamp")
