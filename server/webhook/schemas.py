from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field


class ProblemDetail(BaseModel):
    """Resposta de erro seguindo RFC 7807 Problem Details.

    Usado para todas as respostas de erro do webhook API e MCP.
    Extensoes AIOps: correlation_id e timestamp para rastreabilidade.
    """

    model_config = ConfigDict(frozen=True)

    # Campos RFC 7807
    type: str = "about:blank"
    title: str
    status: int
    detail: str

    # Extensoes AIOps
    correlation_id: str | None = None
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class WebhookResponse(BaseModel):
    """Resposta padrao do webhook para requisicoes aceitas (202)."""

    model_config = ConfigDict(frozen=True)

    status: str  # "accepted"
    incident_id: str | None = None
    correlation_id: str
    deduplicated: bool = False
