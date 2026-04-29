from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


_CANONICAL_SEVERITIES: frozenset[str] = frozenset({"critical", "error", "warning", "info"})


def normalize_severity(value: str) -> str:
    """Normaliza severity para o vocabulário canônico AIOps.

    Vocabulário aceito (case-insensitive): critical, error, warning, info.
    Levanta ValueError se o valor não mapear para nenhuma severity canônica.
    Esta é a fronteira de validação após ADR 0003 — o domínio não valida.
    """
    lower = value.lower()
    if lower not in _CANONICAL_SEVERITIES:
        raise ValueError(
            f"severity must be one of {sorted(_CANONICAL_SEVERITIES)}, got '{value}'"
        )
    return lower


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


class NormalizedAlertDTO(BaseModel):
    """DTO Pydantic da borda HTTP, espelha NormalizedAlert (dataclass).

    Validation ponto canônico para severity após ADR 0003. Normalizers e
    handlers que recebem payload externo constroem este DTO primeiro,
    depois convertem para a entidade de domínio via ``to_domain()``.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    source_id: str
    correlation_id: str
    title: str
    description: str
    severity: str
    alert_type: str
    timestamp: datetime
    detection_timestamp: datetime
    service: str | None = None
    component: str | None = None
    environment: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("severity")
    @classmethod
    def _validate_severity(cls, v: str) -> str:
        return normalize_severity(v)

    def to_domain(self):
        """Converte DTO em NormalizedAlert (dataclass de domínio)."""
        from domain.alert import NormalizedAlert
        return NormalizedAlert(**self.model_dump())
