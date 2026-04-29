"""Estado de incidente passado por todo o pipeline AIOps.

IncidentState é o contexto canônico que atravessa o pipeline Blueprint:
detecção → diagnóstico → mitigação → verificação → resolução. É consumido
por blueprint/, server/webhook, server/routes/a2a, server/api/accuracy,
adapters/persistence, main.py e testes.

Pós ADR 0003: entidade pura — ``@dataclass(frozen=True)`` da stdlib, sem
Pydantic, sem validação de borda, sem dependências externas. Atualizações
imutáveis usam ``dataclasses.replace(state, **kwargs)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from domain.alert import NormalizedAlert

# Verditos validos para o endpoint de accuracy (consumido por server/api/accuracy.py)
VALID_ACCURACY_VERDICTS = frozenset({"correct", "partially_correct", "incorrect", "false_positive"})


@dataclass(frozen=True)
class IncidentState:
    """Contexto de incidente passado por todo o pipeline Blueprint."""

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
    safety_reasons: list[str] = field(default_factory=list)

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
    metadata: dict[str, Any] = field(default_factory=dict)
