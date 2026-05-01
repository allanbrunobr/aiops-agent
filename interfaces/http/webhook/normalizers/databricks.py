from __future__ import annotations

import hmac as hmac_mod
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from application.ports.credential_provider import CredentialProvider
from domain.alert import NormalizedAlert
from interfaces.http.webhook.normalizers.base import AlertNormalizer
from interfaces.http.webhook.normalizers.registry import register_normalizer
from interfaces.http.webhook.schemas import NormalizedAlertDTO

log = structlog.get_logger()

# Mapeamento de tipos de alerta Databricks -> severidade
_SEVERITY_MAP: dict[str, str] = {
    "FATAL": "critical",
    "ERROR": "error",
    "WARN": "warning",
    "INFO": "info",
    # Job run results
    "FAILED": "critical",
    "TIMED_OUT": "error",
    "CANCELLED": "warning",
    "SUCCESS": "info",
    "TRIGGERED": "warning",
    "UNKNOWN": "warning",
}

# Chave de env var para token de autenticacao do Databricks
DATABRICKS_AUTH_TOKEN_KEY = "DATABRICKS_WEBHOOK_TOKEN"


@register_normalizer("databricks")
class DatabricksNormalizer(AlertNormalizer):
    """Normalizador para alertas Databricks (SQL Alerts e Job notifications)."""

    @property
    def source_name(self) -> str:
        return "databricks"

    def validate_auth(
        self,
        headers: dict[str, str],
        body: bytes,
        credentials: CredentialProvider,
    ) -> bool:
        """Valida token de autenticacao do Databricks.

        Databricks pode enviar alertas via webhook com um token
        configurado no webhook destination. Validamos via header
        Authorization: Bearer <token>.
        """
        auth_header = headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            log.warning("databricks.auth_failed", reason="missing Authorization header")
            return False

        received_token = auth_header[7:]
        expected_token = credentials.get(DATABRICKS_AUTH_TOKEN_KEY)

        if expected_token is None:
            log.error("databricks.auth_failed", reason="auth token not configured")
            return False

        is_valid = hmac_mod.compare_digest(received_token, expected_token)
        if not is_valid:
            log.warning("databricks.auth_failed", reason="token mismatch")
        return is_valid

    def normalize(self, payload: dict[str, Any]) -> NormalizedAlert:
        """Converte payload Databricks em NormalizedAlert.

        Suporta dois formatos:
        1. SQL Alert: {"alert_id": ..., "alert_name": ..., "severity": ..., ...}
        2. Job Notification: {"job_id": ..., "run_id": ..., "result_state": ..., ...}
        """
        if "alert_id" in payload:
            return self._normalize_sql_alert(payload)
        elif "job_id" in payload:
            return self._normalize_job_notification(payload)
        else:
            raise ValueError(
                "Databricks payload not recognized: missing 'alert_id' or 'job_id'"
            )

    def _normalize_sql_alert(self, payload: dict[str, Any]) -> NormalizedAlert:
        """Normaliza alerta SQL do Databricks."""
        now = datetime.now(timezone.utc)

        source_id = payload.get("alert_id", uuid.uuid4().hex[:12])
        title = payload.get("alert_name", "Untitled Databricks SQL Alert")
        description = payload.get("message", "")
        severity_raw = payload.get("severity", "INFO")
        severity = _SEVERITY_MAP.get(severity_raw.upper(), "warning")

        triggered_at = payload.get("triggered_at")
        if triggered_at:
            timestamp = datetime.fromisoformat(triggered_at.replace("Z", "+00:00"))
        else:
            timestamp = now

        correlation_id = f"inc-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

        return NormalizedAlertDTO(
            source="databricks",
            source_id=str(source_id),
            correlation_id=correlation_id,
            title=title,
            description=description,
            severity=severity,
            alert_type="databricks.sql_alert",
            timestamp=timestamp,
            detection_timestamp=now,
            service="databricks-sql",
            tags={"state": payload.get("state", "UNKNOWN")},
            metadata={
                "query_name": payload.get("query_name"),
                "alert_type": "sql_alert",
            },
        ).to_domain()

    def _normalize_job_notification(self, payload: dict[str, Any]) -> NormalizedAlert:
        """Normaliza notificacao de job do Databricks."""
        now = datetime.now(timezone.utc)

        source_id = str(payload.get("run_id", uuid.uuid4().hex[:12]))
        run_name = payload.get("run_name", "")
        title = f"Databricks Job: {run_name}" if run_name else "Databricks Job Alert"
        description = payload.get("state_message", "")
        result_state = payload.get("result_state", "UNKNOWN")
        severity = _SEVERITY_MAP.get(result_state.upper(), "warning")

        # Databricks job timestamps sao em milissegundos epoch
        start_time_ms = payload.get("start_time")
        if start_time_ms and isinstance(start_time_ms, (int, float)):
            timestamp = datetime.fromtimestamp(start_time_ms / 1000, tz=timezone.utc)
        else:
            timestamp = now

        correlation_id = f"inc-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

        return NormalizedAlertDTO(
            source="databricks",
            source_id=source_id,
            correlation_id=correlation_id,
            title=title,
            description=description,
            severity=severity,
            alert_type="databricks.job_notification",
            timestamp=timestamp,
            detection_timestamp=now,
            service="databricks-jobs",
            tags={
                "job_id": str(payload.get("job_id", "")),
                "result_state": result_state,
            },
            metadata={
                "run_id": payload.get("run_id"),
                "job_id": payload.get("job_id"),
                "alert_type": "job_notification",
            },
        ).to_domain()
