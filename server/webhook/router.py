from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Request

from core.protocols import CredentialProvider
from server.webhook.dedup import DedupStore, generate_fingerprint
from server.webhook.errors import WebhookError
from server.webhook.normalizers.registry import NormalizerRegistry
from server.webhook.pipeline_runner import PipelineRunner
from server.webhook.schemas import ProblemDetail, WebhookResponse

log = structlog.get_logger()

webhook_router = APIRouter(tags=["webhook"])

# Tamanho maximo de payload padrao (1MB) — configuravel via WebhookConfig
DEFAULT_MAX_PAYLOAD_BYTES = 1_048_576


def identify_source(headers: dict[str, str], payload: dict[str, Any] | None = None) -> str | None:
    """Identifica a fonte do alerta baseado em headers e payload.

    Prioridade:
    1. Header "Alert-Source" (nossa convencao interna)
    2. Header "X-PagerDuty-Signature" (PagerDuty)
    3. Campo "source" no payload JSON

    Retorna o nome da fonte ou None se nao identificavel.
    """
    # Header explicito (nossa convencao)
    alert_source = headers.get("alert-source")
    if alert_source:
        return alert_source.lower()

    # Deteccao por headers especificos de cada fonte
    if "x-pagerduty-signature" in headers:
        return "pagerduty"

    # Fallback: campo source no payload (se disponivel)
    if payload and isinstance(payload.get("source"), str):
        return payload["source"].lower()

    return None


@webhook_router.post("/webhook", status_code=202)
async def receive_webhook(request: Request) -> WebhookResponse:
    """Recebe alerta via webhook com validacao completa.

    Pipeline de validacao:
    1. Verificacao de tamanho do payload
    2. Parse do JSON
    3. Identificacao da fonte
    4. Autenticacao por fonte
    5. Normalizacao do payload
    6. Deduplicacao
    7. Submissao ao pipeline
    8. Retorno 202 Accepted
    """
    correlation_id = f"inc-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    bound_log = log.bind(correlation_id=correlation_id)

    # Obtem configuracao e credenciais do app.state
    config = getattr(request.app.state, "config", None)
    credentials: CredentialProvider | None = getattr(request.app.state, "credentials", None)
    max_payload = DEFAULT_MAX_PAYLOAD_BYTES
    if config and hasattr(config, "webhook"):
        max_payload = getattr(config.webhook, "max_payload_bytes", DEFAULT_MAX_PAYLOAD_BYTES)

    # 1. Verificacao de tamanho via Content-Length header
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > max_payload:
        bound_log.warning(
            "webhook.rejected",
            reason="payload_too_large",
            content_length=content_length,
            max_bytes=max_payload,
        )
        raise WebhookError(ProblemDetail(
            type="urn:aiops:error:payload-too-large",
            title="Payload Too Large",
            status=413,
            detail=f"Payload exceeds maximum size of {max_payload} bytes",
            correlation_id=correlation_id,
        ))

    # 2. Parse JSON (le body uma vez e reutiliza)
    body = await request.body()
    if len(body) > max_payload:
        bound_log.warning("webhook.rejected", reason="payload_too_large_actual")
        raise WebhookError(ProblemDetail(
            type="urn:aiops:error:payload-too-large",
            title="Payload Too Large",
            status=413,
            detail=f"Payload exceeds maximum size of {max_payload} bytes",
            correlation_id=correlation_id,
        ))

    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        bound_log.warning("webhook.rejected", reason="invalid_json", error=str(e))
        raise WebhookError(ProblemDetail(
            type="urn:aiops:error:invalid-payload",
            title="Unprocessable Entity",
            status=422,
            detail=f"Invalid JSON payload: {e}",
            correlation_id=correlation_id,
        ))

    # 3. Identificacao da fonte
    headers = {k.lower(): v for k, v in request.headers.items()}
    source = identify_source(headers, payload)
    if source is None:
        bound_log.warning("webhook.rejected", reason="unknown_source")
        raise WebhookError(ProblemDetail(
            type="urn:aiops:error:unknown-source",
            title="Bad Request",
            status=400,
            detail=(
                "Could not identify alert source from headers or payload. "
                "Provide 'Alert-Source' header or source-specific headers."
            ),
            correlation_id=correlation_id,
        ))

    bound_log = bound_log.bind(source=source)

    # Verifica se normalizer existe para a fonte
    normalizer = NormalizerRegistry.get(source)
    if normalizer is None:
        bound_log.warning("webhook.rejected", reason="unsupported_source")
        raise WebhookError(ProblemDetail(
            type="urn:aiops:error:unsupported-source",
            title="Bad Request",
            status=400,
            detail=(
                f"No normalizer registered for source '{source}'. "
                f"Supported sources: {NormalizerRegistry.list_sources()}"
            ),
            correlation_id=correlation_id,
        ))

    # 4. Autenticacao por fonte
    if credentials is None:
        bound_log.error("webhook.rejected", reason="no_credential_provider")
        raise WebhookError(ProblemDetail(
            type="urn:aiops:error:server-error",
            title="Internal Server Error",
            status=500,
            detail="Credential provider not configured",
            correlation_id=correlation_id,
        ))

    if not normalizer.validate_auth(headers, body, credentials):
        bound_log.warning("webhook.rejected", reason="auth_failed")
        raise WebhookError(ProblemDetail(
            type="urn:aiops:error:unauthorized",
            title="Unauthorized",
            status=401,
            detail=f"Authentication failed for source '{source}'",
            correlation_id=correlation_id,
        ))

    # 5. Normalizacao
    try:
        alert = normalizer.normalize(payload)
    except (ValueError, KeyError) as e:
        bound_log.warning("webhook.rejected", reason="normalization_failed", error=str(e))
        raise WebhookError(ProblemDetail(
            type="urn:aiops:error:validation-failed",
            title="Unprocessable Entity",
            status=422,
            detail=f"Payload validation failed for source '{source}': {e}",
            correlation_id=correlation_id,
        ))

    # 6. Deduplicacao
    dedup_store: DedupStore | None = getattr(request.app.state, "dedup_store", None)
    if dedup_store is not None:
        dedup_window = 300
        if config and hasattr(config, "webhook"):
            dedup_window = getattr(config.webhook, "dedup_window_seconds", 300)

        fingerprint = generate_fingerprint(alert)
        is_dup, original_cid = dedup_store.is_duplicate(fingerprint)

        if is_dup:
            bound_log.info(
                "webhook.deduplicated",
                fingerprint=fingerprint[:16],
                original_correlation_id=original_cid,
            )
            return WebhookResponse(
                status="accepted",
                correlation_id=alert.correlation_id,
                deduplicated=True,
            )

        # Registra fingerprint para futuras deduplicacoes
        dedup_store.record(fingerprint, alert.correlation_id, dedup_window)

    # 7. Submissao ao pipeline
    pipeline_runner: PipelineRunner | None = getattr(request.app.state, "pipeline_runner", None)
    if pipeline_runner is not None:
        accepted = await pipeline_runner.submit(alert)
        if not accepted:
            bound_log.warning("webhook.rejected", reason="queue_full")
            raise WebhookError(ProblemDetail(
                type="urn:aiops:error:service-unavailable",
                title="Service Unavailable",
                status=503,
                detail="Pipeline queue is full. Try again later.",
                correlation_id=correlation_id,
            ))

    # 8. Sucesso — log de auditoria
    bound_log.info(
        "webhook.accepted",
        alert_severity=alert.severity,
        alert_title=alert.title,
        service=alert.service,
    )

    return WebhookResponse(
        status="accepted",
        incident_id=alert.correlation_id,
        correlation_id=alert.correlation_id,
    )
