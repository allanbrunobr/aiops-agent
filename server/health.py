from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

health_router = APIRouter(tags=["health"])

# Registrado no startup do app
_start_time: float = 0.0
_version: str = "0.1.0"


def set_start_time(t: float) -> None:
    """Registra o instante de startup do servidor."""
    global _start_time
    _start_time = t


class HealthResponse(BaseModel):
    """Resposta do endpoint de liveness."""

    model_config = ConfigDict(frozen=True)

    status: str
    version: str
    uptime_seconds: float
    timestamp: str


class ComponentStatus(BaseModel):
    """Status de um componente individual."""

    model_config = ConfigDict(frozen=True)

    name: str
    healthy: bool
    detail: str | None = None


class ReadinessResponse(BaseModel):
    """Resposta do endpoint de readiness."""

    model_config = ConfigDict(frozen=True)

    status: str  # "ready" | "degraded" | "not_ready"
    components: list[ComponentStatus]
    active_pipelines: int = 0
    queued_alerts: int = 0
    timestamp: str


@health_router.get("/health")
async def health() -> HealthResponse:
    """Liveness check — rapido, sem dependencias externas."""
    return HealthResponse(
        status="ok",
        version=_version,
        uptime_seconds=round(time.monotonic() - _start_time, 2),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@health_router.get("/health/ready")
async def readiness(request: Request) -> ReadinessResponse:
    """Readiness check — verifica componentes criticos."""
    components: list[ComponentStatus] = []

    # Webhook subsystem
    components.append(ComponentStatus(
        name="webhook",
        healthy=True,
        detail="accepting requests",
    ))

    # Verifica disponibilidade de componentes via app.state
    store = getattr(request.app.state, "store", None)
    components.append(ComponentStatus(
        name="store",
        healthy=store is not None,
        detail="in-memory" if store else "not configured",
    ))

    creds = getattr(request.app.state, "credentials", None)
    components.append(ComponentStatus(
        name="credentials",
        healthy=creds is not None,
        detail="env-provider" if creds else "not configured",
    ))

    # Pipeline runner (adicionado pela Story 2.6)
    pipeline_runner = getattr(request.app.state, "pipeline_runner", None)
    active_pipelines = 0
    queued_alerts = 0
    if pipeline_runner is not None:
        active_pipelines = pipeline_runner.active_count
        queued_alerts = pipeline_runner.queued_count
        components.append(ComponentStatus(
            name="pipeline_runner",
            healthy=True,
            detail=f"{active_pipelines} active, {queued_alerts} queued",
        ))

    # Determina status geral
    all_healthy = all(c.healthy for c in components)
    any_healthy = any(c.healthy for c in components)

    if all_healthy:
        overall = "ready"
    elif any_healthy:
        overall = "degraded"
    else:
        overall = "not_ready"

    return ReadinessResponse(
        status=overall,
        components=components,
        active_pipelines=active_pipelines,
        queued_alerts=queued_alerts,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
