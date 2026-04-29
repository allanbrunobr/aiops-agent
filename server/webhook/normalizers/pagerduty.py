from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from core.protocols import CredentialProvider
from domain.alert import NormalizedAlert
from server.webhook.normalizers.base import AlertNormalizer
from server.webhook.normalizers.registry import register_normalizer

log = structlog.get_logger()

# Mapeamento de severidade PagerDuty -> NormalizedAlert
_SEVERITY_MAP: dict[str, str] = {
    "critical": "critical",
    "error": "error",
    "warning": "warning",
    "info": "info",
    # PagerDuty usa "high"/"low" em alguns contextos
    "high": "critical",
    "low": "warning",
}

# Chave de env var para segredo HMAC do PagerDuty
PAGERDUTY_HMAC_SECRET_KEY = "PAGERDUTY_WEBHOOK_SECRET"


@register_normalizer("pagerduty")
class PagerDutyNormalizer(AlertNormalizer):
    """Normalizador para webhooks PagerDuty V2 Events API."""

    @property
    def source_name(self) -> str:
        return "pagerduty"

    def validate_auth(
        self,
        headers: dict[str, str],
        body: bytes,
        credentials: CredentialProvider,
    ) -> bool:
        """Valida assinatura HMAC-SHA256 do PagerDuty.

        PagerDuty envia o header X-PagerDuty-Signature com formato:
        v1=<hex_digest>

        A assinatura e calculada com HMAC-SHA256 usando o webhook secret
        configurado no PagerDuty.
        """
        signature_header = headers.get("x-pagerduty-signature", "")
        if not signature_header:
            log.warning("pagerduty.auth_failed", reason="missing signature header")
            return False

        secret = credentials.get(PAGERDUTY_HMAC_SECRET_KEY)
        if secret is None:
            log.error("pagerduty.auth_failed", reason="HMAC secret not configured")
            return False

        # PagerDuty usa formato "v1=<signature>"
        parts = signature_header.split("=", 1)
        if len(parts) != 2 or parts[0] != "v1":
            log.warning("pagerduty.auth_failed", reason="invalid signature format")
            return False

        received_signature = parts[1]
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        is_valid = hmac.compare_digest(received_signature, expected_signature)
        if not is_valid:
            log.warning("pagerduty.auth_failed", reason="signature mismatch")
        return is_valid

    def normalize(self, payload: dict[str, Any]) -> NormalizedAlert:
        """Converte payload PagerDuty V2 em NormalizedAlert.

        Formato esperado (PagerDuty V2 webhook):
        {
            "event": {
                "id": "...",
                "event_type": "incident.triggered",
                "occurred_at": "2026-03-08T...",
                "data": {
                    "id": "P123ABC",
                    "title": "High CPU on payment-svc",
                    "urgency": "high",
                    "service": {"summary": "payment-svc"},
                    ...
                }
            }
        }
        """
        event = payload.get("event", {})
        data = event.get("data", {})

        if not data:
            raise ValueError("PagerDuty payload missing 'event.data'")

        # Extrai campos
        source_id = data.get("id", "")
        title = data.get("title", "Untitled PagerDuty Alert")
        description = data.get("description") or data.get("title", "")
        urgency = data.get("urgency", "info")
        severity = _SEVERITY_MAP.get(urgency.lower(), "warning")

        # Timestamp
        occurred_at = event.get("occurred_at")
        if occurred_at:
            timestamp = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        else:
            timestamp = datetime.now(timezone.utc)

        # Servico
        service_info = data.get("service", {})
        service = service_info.get("summary") if isinstance(service_info, dict) else None

        # Tipo do alerta
        alert_type = event.get("event_type", "incident.triggered")

        # Correlation ID
        now = datetime.now(timezone.utc)
        correlation_id = f"inc-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

        return NormalizedAlert(
            source="pagerduty",
            source_id=source_id,
            correlation_id=correlation_id,
            title=title,
            description=description,
            severity=severity,
            alert_type=alert_type,
            timestamp=timestamp,
            detection_timestamp=now,
            service=service,
            tags={"urgency": urgency},
            metadata={"raw_event_type": alert_type},
        )
