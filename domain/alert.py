"""Contrato compartilhado de alerta normalizado.

NormalizedAlert é o esquema unificado produzido pelos normalizadores de
webhook (PagerDuty, Grafana, Databricks) e consumido por todo o pipeline:
server/webhook, mcp_server, server/routes/a2a, blueprint, core/store.

NOTA: temporariamente Pydantic-backed. Mantém-se como BaseModel por enquanto
para preservar validações e a interface dos 33 import sites existentes em
``core/models.py`` sem refactor amplo. Em fase posterior pode ser separado
em duas representações: dataclass na camada de domínio + DTO Pydantic na
borda HTTP.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class NormalizedAlert(BaseModel):
    """Esquema unificado de alerta produzido por todos os normalizadores."""

    model_config = ConfigDict(frozen=True)

    # Identificacao da fonte
    source: str           # "pagerduty", "grafana", "databricks"
    source_id: str        # ID original no sistema fonte
    correlation_id: str   # Formato: inc-{YYYYMMDD}-{uuid4_short_8chars}

    # Conteudo do alerta
    title: str
    description: str
    severity: str         # "critical", "error", "warning", "info"
    alert_type: str       # Categoria especifica da fonte (e.g., "HighErrorRate")

    # Timeline
    timestamp: datetime           # Quando o alerta ocorreu (ISO 8601 com timezone)
    detection_timestamp: datetime  # Quando o sistema detectou

    # Contexto (opcionais)
    service: str | None = None
    component: str | None = None
    environment: str | None = None

    # Metadata extensivel
    tags: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("severity")
    @classmethod
    def validate_severity(cls, v: str) -> str:
        """Severity deve ser um dos valores permitidos."""
        allowed = {"critical", "error", "warning", "info"}
        v_lower = v.lower()
        if v_lower not in allowed:
            raise ValueError(f"severity must be one of {allowed}, got '{v}'")
        return v_lower
