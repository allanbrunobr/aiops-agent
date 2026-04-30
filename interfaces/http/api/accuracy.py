from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from application.ports.incident_store import IncidentStore
from domain.incident_state import VALID_ACCURACY_VERDICTS, IncidentState

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1", tags=["accuracy"])


class AccuracyReviewRequest(BaseModel):
    """Payload para revisão de acurácia de diagnóstico."""

    accuracy: str
    notes: str = ""

    @field_validator("accuracy")
    @classmethod
    def validate_accuracy(cls, v: str) -> str:
        """Valida que o valor de acurácia é uma categoria permitida."""
        if v not in VALID_ACCURACY_VERDICTS:
            raise ValueError(
                f"accuracy must be one of {VALID_ACCURACY_VERDICTS}, got '{v}'"
            )
        return v


class AccuracyReviewResponse(BaseModel):
    """Resposta após revisão de acurácia."""

    correlation_id: str
    accuracy_verdict: str
    reviewed_at: str
    message: str


class AccuracyMetricsResponse(BaseModel):
    """Métricas agregadas de acurácia."""

    total_incidents: int
    total_reviewed: int
    total_pending: int
    accuracy_rate: float
    false_positive_rate: float
    category_breakdown: dict[str, int]
    incidents: list[dict[str, Any]]


# --- Dependency injection ---

def get_store() -> IncidentStore:
    """Placeholder — substituído pelo composition root em server/app.py."""
    raise NotImplementedError("Store must be injected via app.dependency_overrides")


# --- Endpoints ---

@router.patch("/incidents/{correlation_id}/accuracy", response_model=AccuracyReviewResponse)
async def update_accuracy(
    correlation_id: str,
    body: AccuracyReviewRequest,
    store: IncidentStore = Depends(get_store),
) -> AccuracyReviewResponse:
    """Atualiza veredito de acurácia de um diagnóstico."""
    state = store.get(correlation_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Incident {correlation_id} not found")

    now = datetime.utcnow()

    updated = dataclasses.replace(
        state,
        accuracy_status="reviewed",
        accuracy_verdict=body.accuracy,
        accuracy_notes=body.notes,
        reviewed_at=now,
    )
    store.save(correlation_id, updated)

    # Tratamento especial para falso positivo (Story 4.5)
    if body.accuracy == "false_positive":
        try:
            from interfaces.http.handlers.false_positive import handle_false_positive_side_effects
            await handle_false_positive_side_effects(correlation_id, body.notes, updated)
        except Exception:
            pass  # side-effects não bloqueiam a resposta principal

    log.info(
        "accuracy.reviewed",
        correlation_id=correlation_id,
        verdict=body.accuracy,
    )

    return AccuracyReviewResponse(
        correlation_id=correlation_id,
        accuracy_verdict=body.accuracy,
        reviewed_at=now.isoformat() + "Z",
        message="Accuracy review recorded",
    )


@router.get("/metrics/accuracy", response_model=AccuracyMetricsResponse)
async def get_accuracy_metrics(
    store: IncidentStore = Depends(get_store),
) -> AccuracyMetricsResponse:
    """Retorna métricas agregadas de acurácia dos diagnósticos."""
    all_incidents = _get_all_incidents(store)

    total = len(all_incidents)
    reviewed = [s for s in all_incidents if s.accuracy_status == "reviewed"]
    pending = [s for s in all_incidents if s.accuracy_status == "pending_review"]

    breakdown: dict[str, int] = {
        "correct": 0,
        "partially_correct": 0,
        "incorrect": 0,
        "false_positive": 0,
    }
    for s in reviewed:
        if s.accuracy_verdict and s.accuracy_verdict in breakdown:
            breakdown[s.accuracy_verdict] += 1

    accuracy_rate = 0.0
    false_positive_rate = 0.0
    if reviewed:
        accuracy_rate = round(breakdown["correct"] / len(reviewed), 4)
        false_positive_rate = round(breakdown["false_positive"] / len(reviewed), 4)

    incidents_summary = [
        {
            "correlation_id": s.correlation_id,
            "accuracy_status": s.accuracy_status,
            "accuracy_verdict": s.accuracy_verdict,
            "confidence": s.diagnosis.get("confidence") if s.diagnosis else None,
            "reviewed_at": s.reviewed_at.isoformat() + "Z" if s.reviewed_at else None,
        }
        for s in sorted(
            all_incidents,
            key=lambda x: x.reviewed_at or datetime.min,
            reverse=True,
        )
    ]

    return AccuracyMetricsResponse(
        total_incidents=total,
        total_reviewed=len(reviewed),
        total_pending=len(pending),
        accuracy_rate=accuracy_rate,
        false_positive_rate=false_positive_rate,
        category_breakdown=breakdown,
        incidents=incidents_summary,
    )


def _get_all_incidents(store: IncidentStore) -> list[IncidentState]:
    """Recupera todos os incidentes do store (ativos + terminais).

    Nota: IncidentStore.list_active() retorna apenas não-terminais.
    Para métricas de acurácia, precisamos de TODOS os incidentes.
    """
    if hasattr(store, "list_all"):
        return [state for _, state in store.list_all()]
    return [state for _, state in store.list_active()]
