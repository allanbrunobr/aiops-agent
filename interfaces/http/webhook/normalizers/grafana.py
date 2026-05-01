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

# Mapeamento de estado Grafana -> severidade NormalizedAlert
_SEVERITY_MAP: dict[str, str] = {
    "alerting": "critical",
    "firing": "critical",
    "no_data": "warning",
    "pending": "info",
    "ok": "info",
    "paused": "info",
    "normal": "info",
    "resolved": "info",
}

# Chave de env var para token de autenticacao do Grafana
GRAFANA_AUTH_TOKEN_KEY = "GRAFANA_WEBHOOK_TOKEN"


@register_normalizer("grafana")
class GrafanaNormalizer(AlertNormalizer):
    """Normalizador para webhooks Grafana Alerting."""

    @property
    def source_name(self) -> str:
        return "grafana"

    def validate_auth(
        self,
        headers: dict[str, str],
        body: bytes,
        credentials: CredentialProvider,
    ) -> bool:
        """Valida Bearer token do Grafana.

        Grafana envia o header Authorization: Bearer <token>
        configurado no contact point do webhook.
        """
        auth_header = headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            log.warning("grafana.auth_failed", reason="missing or invalid Authorization header")
            return False

        received_token = auth_header[7:]  # Remove "Bearer "
        expected_token = credentials.get(GRAFANA_AUTH_TOKEN_KEY)

        if expected_token is None:
            log.error("grafana.auth_failed", reason="auth token not configured")
            return False

        # Comparacao em tempo constante para evitar timing attacks
        is_valid = hmac_mod.compare_digest(received_token, expected_token)
        if not is_valid:
            log.warning("grafana.auth_failed", reason="token mismatch")
        return is_valid

    def normalize(self, payload: dict[str, Any]) -> NormalizedAlert:
        """Converte payload Grafana Alerting em NormalizedAlert.

        Formato esperado (Grafana Unified Alerting webhook):
        {
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {"alertname": "HighCPU", "severity": "critical"},
                    "annotations": {"summary": "...", "description": "..."},
                    "startsAt": "2026-03-08T...",
                    "fingerprint": "abc123",
                    ...
                }
            ],
            "commonLabels": {"alertname": "HighCPU"},
            ...
        }

        Nota: Grafana envia array de alerts; processamos o primeiro.
        """
        alerts = payload.get("alerts", [])
        if not alerts:
            raise ValueError("Grafana payload missing 'alerts' array or empty")

        alert = alerts[0]  # Processa primeiro alerta do grupo
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})

        # Campos basicos
        title = labels.get("alertname", "Untitled Grafana Alert")
        description = annotations.get("description") or annotations.get("summary", "")
        status = alert.get("status", "firing")
        severity_label = labels.get("severity", "")
        severity = _SEVERITY_MAP.get(severity_label.lower(), _SEVERITY_MAP.get(status, "warning"))

        # Timestamp
        starts_at = alert.get("startsAt")
        if starts_at:
            timestamp = datetime.fromisoformat(starts_at.replace("Z", "+00:00"))
        else:
            timestamp = datetime.now(timezone.utc)

        # Servico e componente dos labels
        service = labels.get("service") or labels.get("job")
        component = labels.get("component") or labels.get("instance")
        environment = labels.get("environment") or labels.get("env")

        # Fingerprint como source_id
        source_id = alert.get("fingerprint", uuid.uuid4().hex[:12])

        now = datetime.now(timezone.utc)
        correlation_id = f"inc-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

        return NormalizedAlertDTO(
            source="grafana",
            source_id=source_id,
            correlation_id=correlation_id,
            title=title,
            description=description,
            severity=severity,
            alert_type=f"grafana.{status}",
            timestamp=timestamp,
            detection_timestamp=now,
            service=service,
            component=component,
            environment=environment,
            tags={k: str(v) for k, v in labels.items()},
            metadata={
                "alert_count": len(alerts),
                "common_labels": payload.get("commonLabels", {}),
            },
        ).to_domain()
