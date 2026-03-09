from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from server.metrics.pipeline import PipelineMetricsCollector

router = APIRouter(prefix="/api/v1", tags=["metrics"])


def get_metrics_collector() -> PipelineMetricsCollector:
    """Placeholder — substituído pelo composition root em server/app.py."""
    raise NotImplementedError("Collector must be injected via app.dependency_overrides")


@router.get("/metrics/pipeline")
async def get_pipeline_metrics(
    collector: PipelineMetricsCollector = Depends(get_metrics_collector),
) -> dict[str, Any]:
    """Retorna métricas agregadas de execução de pipelines.

    Inclui contagens (completed/failed/in-progress), distribuição de latência
    (p50/p90/p99), e duração por estágio.
    """
    return collector.get_summary()
