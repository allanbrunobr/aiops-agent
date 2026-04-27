"""Modelos Pydantic compartilhados do pipeline AIOps.

Define IncidentState e re-exporta NormalizedAlert/MitigationAction — contratos
de dados usados por todos os modulos do pipeline Blueprint. Validacao em
runtime para ingestao de webhooks e serializacao JSON para audit logs (NFR18).

NormalizedAlert vive em domain/alert.py e MitigationAction em domain/incident.py;
ambos sao re-exportados aqui para preservar os import sites existentes
(`from core.models import NormalizedAlert, MitigationAction`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from domain.alert import NormalizedAlert
from domain.incident import MitigationAction

__all__ = [
    "VALID_ACCURACY_VERDICTS",
    "NormalizedAlert",
    "IncidentState",
    "MitigationAction",
]

# Verditos validos para o endpoint de accuracy (importado por server/api/accuracy.py)
VALID_ACCURACY_VERDICTS = frozenset({"correct", "partially_correct", "incorrect", "false_positive"})


class IncidentState(BaseModel):
    """Contexto de incidente passado por todo o pipeline Blueprint."""

    model_config = ConfigDict(populate_by_name=True)

    # Identificadores
    incident_id: str        # Formato: inc-{YYYYMMDD}-{uuid4_short_8chars}
    correlation_id: str     # Igual a NormalizedAlert.correlation_id

    # Alerta de origem
    alert: NormalizedAlert

    # Timeline
    received_at: datetime
    started_at: datetime    # = alert.timestamp

    # Estado do pipeline
    state: str = "DETECTING"  # DETECTING|DIAGNOSING|MITIGATING|VERIFYING|RESOLVED

    # Resultados do pipeline (preenchidos por nodes)
    aggregated_report: dict[str, Any] | None = None
    diagnosis: dict[str, Any] | None = None        # root_cause, evidence, confidence, timeline
    proposed_action: dict[str, Any] | None = None   # action_type, command, rollback

    # Seguranca
    safety_verdict: str | None = None               # "APPROVED", "BLOCKED", "NEEDS_REVIEW"
    safety_reasons: list[str] = Field(default_factory=list)

    # Saude pos-acao (TNR)
    pre_action_health: dict[str, Any] | None = None
    post_action_health: dict[str, Any] | None = None
    regression_detected: bool = False

    # Postmortem
    postmortem_md: str | None = None

    # Flags de modo
    no_llm: bool = False
    no_rag: bool = False

    # Acurácia de diagnóstico (Story 3.3)
    accuracy_status: str = "pending_review"  # "pending_review" | "reviewed"
    accuracy_verdict: str | None = None      # "correct" | "partially_correct" | "incorrect" | "false_positive"
    accuracy_notes: str | None = None
    reviewed_at: datetime | None = None

    # Dados brutos e resultados de processamento (usado pelo sanitizer adapter e testes)
    metadata: dict[str, Any] = Field(default_factory=dict)
