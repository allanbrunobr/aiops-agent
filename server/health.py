from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(tags=["health"])

# Referência injetada pelo composition root
_metrics_collector = None


def set_metrics_collector(collector: Any) -> None:
    """Configura referência ao coletor de métricas para health/ready."""
    global _metrics_collector
    _metrics_collector = collector


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness check — processo está vivo."""
    return {"status": "ok"}


@router.get("/health/ready")
async def health_ready() -> dict[str, Any]:
    """Readiness check — sistema pronto para receber alertas.

    Inclui contagem de pipelines ativos e timestamp do último alerta (NFR19).
    """
    response: dict[str, Any] = {"status": "ready"}

    if _metrics_collector is not None:
        response["active_pipelines"] = _metrics_collector.get_active_count()
        last_alert = _metrics_collector.get_last_alert_at()
        response["last_alert_at"] = (
            last_alert.isoformat() + "Z" if last_alert else None
        )

    return response
