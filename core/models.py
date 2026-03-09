"""Modelos centrais do AIOps Agent V1.

Define IncidentState (modelo de estado do pipeline) e NormalizedAlert
(alerta normalizado de qualquer fonte).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class NormalizedAlert(BaseModel):
    """Alerta normalizado de qualquer fonte (PagerDuty, Grafana, simulado, etc.)."""
    source: str
    source_id: str
    correlation_id: str
    title: str
    description: str = ""
    severity: str = "medium"
    alert_type: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    detection_timestamp: datetime | None = None
    service: str = ""
    environment: str = "production"
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class IncidentState(BaseModel):
    """Estado completo do incidente durante o pipeline.

    Modelo imutavel — usar model_copy(update={...}) para criar novas versoes.
    """
    incident_id: str
    correlation_id: str
    alert: NormalizedAlert
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None

    # Flags de modo
    no_llm: bool = False
    no_rag: bool = False

    # Dados do pipeline
    aggregated_report: dict[str, Any] | None = None
    diagnosis: dict[str, Any] | None = None
    postmortem_md: str | None = None

    # Acao proposta e safety
    proposed_action: dict[str, Any] | None = None
    safety_verdict: str | None = None
    safety_reasons: list[str] = Field(default_factory=list)

    # TNR
    pre_action_health: dict[str, Any] | None = None
    post_action_health: dict[str, Any] | None = None
    regression_detected: bool = False

    # Metadata generico
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}
