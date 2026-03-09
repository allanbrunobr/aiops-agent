from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# Categorias de acurácia válidas
AccuracyVerdict = Literal["correct", "partially_correct", "incorrect", "false_positive"]

# Valores válidos como set para validação em runtime
VALID_ACCURACY_VERDICTS = {"correct", "partially_correct", "incorrect", "false_positive"}


class NormalizedAlert(BaseModel):
    """Esquema unificado de alerta produzido por todos os normalizadores."""

    model_config = ConfigDict(frozen=True)

    # Identificação da fonte
    source: str
    source_id: str
    correlation_id: str

    # Conteúdo do alerta
    title: str
    description: str
    severity: str
    alert_type: str

    # Timeline
    timestamp: datetime
    detection_timestamp: datetime

    # Contexto (opcionais)
    affected_services: list[str] = Field(default_factory=list)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class IncidentState(BaseModel):
    """Contexto de incidente passado por todo o pipeline Blueprint."""

    model_config = ConfigDict(populate_by_name=True)

    # Identificadores
    incident_id: str
    correlation_id: str

    # Alerta de origem
    alert: NormalizedAlert

    # Timeline
    received_at: datetime
    started_at: datetime

    # Estado do pipeline
    state: str = "DETECTING"

    # Resultados do pipeline (preenchidos por nodes)
    aggregated_report: dict[str, Any] | None = None
    diagnosis: dict[str, Any] | None = None
    proposed_action: dict[str, Any] | None = None

    # Segurança
    safety_verdict: str | None = None
    safety_reasons: list[str] = Field(default_factory=list)

    # Saúde pós-ação (TNR)
    pre_action_health: dict[str, Any] | None = None
    post_action_health: dict[str, Any] | None = None
    regression_detected: bool = False

    # Postmortem
    postmortem_md: str | None = None

    # Flags de modo
    no_llm: bool = False
    no_rag: bool = False

    # Tracking de acurácia (Story 3.3)
    accuracy_status: str = "pending_review"  # "pending_review" | "reviewed"
    accuracy_verdict: str | None = None       # correct | partially_correct | incorrect | false_positive
    accuracy_notes: str | None = None
    reviewed_at: datetime | None = None
