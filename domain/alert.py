"""Contrato compartilhado de alerta normalizado.

NormalizedAlert é o esquema unificado produzido pelos normalizadores de
webhook (PagerDuty, Grafana, Databricks) e consumido por todo o pipeline.

Validação de severity ocorre na borda HTTP (server/webhook/schemas.NormalizedAlertDTO
+ helper ``normalize_severity``) ou nos próprios normalizers, que usam mapas para
o vocabulário canônico {critical, error, warning, info}. O domínio confia que a
entrada já está normalizada — qualquer construção programática que precise de
validação deve usar o DTO da borda.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class NormalizedAlert:
    """Esquema unificado de alerta produzido por todos os normalizadores."""

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
    tags: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
