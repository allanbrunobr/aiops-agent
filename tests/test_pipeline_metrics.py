from __future__ import annotations

"""
Testes para Story 3.5: Pipeline Processing Metrics.
Cobre PipelineMetricsCollector e endpoint GET /api/v1/metrics/pipeline.
"""

import asyncio
import datetime

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

from core.lifecycle import LifecycleEvent
from server.metrics.pipeline import PipelineMetricsCollector, _percentile
from server.api.metrics import router, get_metrics_collector
from fastapi import FastAPI


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_event(
    event_type: str,
    correlation_id: str = "corr-001",
    node_name: str | None = None,
    data: dict | None = None,
    offset_seconds: int = 0,
) -> LifecycleEvent:
    """Cria LifecycleEvent para testes com timestamp recente (para evitar pruning)."""
    ts = datetime.datetime.utcnow() + datetime.timedelta(seconds=offset_seconds)
    return LifecycleEvent(
        event_type=event_type,
        node_name=node_name,
        correlation_id=correlation_id,
        timestamp=ts,
        data=data or {},
    )


def _make_app(collector: PipelineMetricsCollector) -> FastAPI:
    """Cria FastAPI app com router de métricas e collector injetado."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_metrics_collector] = lambda: collector
    return app


# ── _percentile ───────────────────────────────────────────────────────────────

def test_percentile_single_value():
    """Percentil de lista com 1 elemento retorna esse elemento."""
    assert _percentile([100], 50) == 100.0


def test_percentile_empty_list():
    """Percentil de lista vazia retorna 0.0."""
    assert _percentile([], 90) == 0.0


def test_percentile_p50_two_values():
    """p50 de [10, 20] = 15.0 (interpolação linear)."""
    assert _percentile([10, 20], 50) == 15.0


def test_percentile_p90_sorted():
    """p90 de lista [1..10] deve ser próximo a 9."""
    values = list(range(1, 11))  # [1, 2, ..., 10]
    result = _percentile(values, 90)
    assert 9.0 <= result <= 10.0


# ── PipelineMetricsCollector ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_on_event_pipeline_started_creates_record():
    """pipeline.started deve criar registro no collector."""
    collector = PipelineMetricsCollector()

    await collector.on_event(_make_event("pipeline.started", "corr-001"))

    counts = collector.get_pipeline_counts()
    assert counts["in_progress"] == 1
    assert collector.get_active_count() == 1


@pytest.mark.asyncio
async def test_on_event_pipeline_completed_updates_status():
    """pipeline.completed deve atualizar status e duration."""
    collector = PipelineMetricsCollector()

    await collector.on_event(_make_event("pipeline.started", "corr-001"))
    await collector.on_event(_make_event(
        "pipeline.completed", "corr-001",
        data={"total_duration_ms": 500},
        offset_seconds=1,
    ))

    counts = collector.get_pipeline_counts()
    assert counts["completed"] == 1
    assert counts["in_progress"] == 0

    latency = collector.get_latency_distribution()
    assert latency["p50"] == 500.0


@pytest.mark.asyncio
async def test_on_event_pipeline_failed_updates_status():
    """pipeline.failed deve atualizar status para failed."""
    collector = PipelineMetricsCollector()

    await collector.on_event(_make_event("pipeline.started", "corr-err"))
    await collector.on_event(_make_event(
        "pipeline.failed", "corr-err",
        data={"total_duration_ms": 200},
        offset_seconds=1,
    ))

    counts = collector.get_pipeline_counts()
    assert counts["failed"] == 1
    assert counts["in_progress"] == 0


@pytest.mark.asyncio
async def test_on_event_node_completed_records_stage_duration():
    """node.completed deve registrar duração por estágio."""
    collector = PipelineMetricsCollector()

    await collector.on_event(_make_event("pipeline.started", "corr-001"))
    await collector.on_event(_make_event(
        "node.completed", "corr-001",
        node_name="aggregator",
        data={"duration_ms": 150},
        offset_seconds=1,
    ))
    await collector.on_event(_make_event(
        "pipeline.completed", "corr-001",
        data={"total_duration_ms": 300},
        offset_seconds=2,
    ))

    stage_durations = collector.get_stage_durations()
    assert "aggregator" in stage_durations
    assert stage_durations["aggregator"]["avg"] == 150.0


@pytest.mark.asyncio
async def test_latency_empty_no_completed():
    """Sem pipelines completos, latência deve retornar None em todos os percentis."""
    collector = PipelineMetricsCollector()
    latency = collector.get_latency_distribution()
    assert latency["p50"] is None
    assert latency["p90"] is None
    assert latency["p99"] is None


@pytest.mark.asyncio
async def test_get_last_alert_at_updates_on_pipeline_started():
    """last_alert_at deve ser atualizado ao receber pipeline.started."""
    collector = PipelineMetricsCollector()
    assert collector.get_last_alert_at() is None

    ts = datetime.datetime(2026, 3, 4, 14, 0, 0)
    await collector.on_event(LifecycleEvent(
        event_type="pipeline.started",
        node_name=None,
        correlation_id="corr-ts",
        timestamp=ts,
    ))

    assert collector.get_last_alert_at() == ts


# ── GET /api/v1/metrics/pipeline ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_pipeline_metrics_endpoint():
    """GET /api/v1/metrics/pipeline retorna estrutura esperada."""
    collector = PipelineMetricsCollector()

    # Simula um pipeline completo
    await collector.on_event(_make_event("pipeline.started", "corr-001"))
    await collector.on_event(_make_event(
        "pipeline.completed", "corr-001",
        data={"total_duration_ms": 1000},
    ))

    app = _make_app(collector)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/metrics/pipeline")

    assert resp.status_code == 200
    data = resp.json()
    assert "counts" in data
    assert "latency" in data
    assert "stage_durations" in data
    assert "active_count" in data
    assert data["counts"]["completed"] == 1
