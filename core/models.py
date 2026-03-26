"""Modelos Pydantic compartilhados do pipeline AIOps.

Define NormalizedAlert e IncidentState — contratos de dados usados por
todos os modulos do pipeline Blueprint. Validacao em runtime para
ingestao de webhooks e serializacao JSON para audit logs (NFR18).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Verditos validos para o endpoint de accuracy (importado por server/api/accuracy.py)
VALID_ACCURACY_VERDICTS = frozenset({"correct", "partially_correct", "incorrect", "false_positive"})


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


class MitigationAction(dict):
    """Uma ação de mitigação proposta pelo agente.

    Implementa dict para compatibilidade com avaliadores que lêem action como dict,
    e expõe atributos via __getattr__ para compatibilidade com código que usa action.field.
    """

    def __init__(
        self,
        description: str,
        command: str,
        risk_level: str,
        rollback_command: str | None = None,
    ):
        super().__init__(
            description=description,
            command=command,
            risk_level=risk_level,
            rollback_command=rollback_command,
        )

    def __getattr__(self, name: str):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name: str, value) -> None:
        self[name] = value
