"""Testes para Story 2.6: Concurrent Alert Processing and Pipeline Trigger.

PipelineRunner usa asyncio.Semaphore/Queue — todos os testes
devem rodar APENAS com asyncio (nao trio).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from domain.alert import NormalizedAlert
from interfaces.http.webhook.pipeline_runner import PipelineRunner


# Restringe este modulo ao backend asyncio
@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


def _make_alert(cid: str = "inc-20260308-test0001") -> NormalizedAlert:
    """Helper para criar NormalizedAlert de teste."""
    return NormalizedAlert(
        source="test",
        source_id="T1",
        correlation_id=cid,
        title="Test Alert",
        description="Test",
        severity="warning",
        alert_type="test",
        timestamp=datetime.now(timezone.utc),
        detection_timestamp=datetime.now(timezone.utc),
    )


@pytest.mark.anyio
async def test_submit_triggers_pipeline():
    """AC1: submit aceita alerta e dispara pipeline."""
    runner = PipelineRunner(max_concurrent=3, max_queued=10)
    await runner.start()
    try:
        alert = _make_alert()
        accepted = await runner.submit(alert)
        assert accepted is True
        await asyncio.sleep(0.05)
    finally:
        await runner.stop()


@pytest.mark.anyio
async def test_webhook_returns_202_before_pipeline():
    """AC1: submit retorna imediatamente sem bloquear."""
    import time
    runner = PipelineRunner(max_concurrent=3, max_queued=10)
    await runner.start()
    try:
        alert = _make_alert()
        start = time.monotonic()
        accepted = await runner.submit(alert)
        elapsed_ms = (time.monotonic() - start) * 1000
        assert accepted is True
        assert elapsed_ms < 200
    finally:
        await runner.stop()


@pytest.mark.anyio
async def test_active_count_starts_at_zero():
    """AC2: contagem de pipelines começa em zero."""
    runner = PipelineRunner(max_concurrent=3, max_queued=10)
    await runner.start()
    try:
        assert runner.active_count == 0
        assert runner.queued_count == 0
    finally:
        await runner.stop()


@pytest.mark.anyio
async def test_pipeline_isolation_on_failure():
    """AC3: falha em um pipeline nao afeta outros."""
    runner = PipelineRunner(max_concurrent=3, max_queued=10)
    await runner.start()

    completed = []

    async def patched_execute(alert, bound_log):
        if alert.correlation_id == "inc-fail":
            raise RuntimeError("Simulated pipeline failure")
        await asyncio.sleep(0)
        completed.append(alert.correlation_id)

    runner._execute_pipeline = patched_execute

    try:
        await runner.submit(_make_alert("inc-fail"))
        await runner.submit(_make_alert("inc-ok-1"))
        await runner.submit(_make_alert("inc-ok-2"))
        await asyncio.sleep(0.1)
        assert "inc-ok-1" in completed
        assert "inc-ok-2" in completed
    finally:
        await runner.stop()


@pytest.mark.anyio
async def test_queue_full_returns_false():
    """AC6: fila cheia retorna False."""
    barrier = asyncio.Event()

    async def blocking_execute(alert, bound_log):
        await barrier.wait()

    # max_concurrent=1, max_queued=1: slot para 1 ativo + 1 na fila
    runner = PipelineRunner(max_concurrent=1, max_queued=1)
    runner._execute_pipeline = blocking_execute
    await runner.start()

    try:
        # Ocupa o unico slot concorrente
        r1 = await runner.submit(_make_alert("inc-1"))
        assert r1 is True
        # Aguarda pipeline iniciar e adquirir semaforo
        await asyncio.sleep(0.05)

        # Preenche a fila (max_queued=1)
        r2 = await runner.submit(_make_alert("inc-2"))
        assert r2 is True

        # Terceiro deve ser rejeitado (slot tomado + fila cheia)
        r3 = await runner.submit(_make_alert("inc-3"))
        assert r3 is False
    finally:
        barrier.set()
        await runner.stop()


@pytest.mark.anyio
async def test_health_ready_shows_pipeline_counts(app):
    """AC7: /health/ready mostra contagens de pipelines ativos."""
    from httpx import ASGITransport, AsyncClient

    runner = PipelineRunner(max_concurrent=3, max_queued=10)
    await runner.start()
    app.state.pipeline_runner = runner

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            resp = await client.get("/health/ready")
            data = resp.json()
            assert "active_pipelines" in data
            assert "queued_alerts" in data
            assert data["active_pipelines"] >= 0
            assert data["queued_alerts"] >= 0
    finally:
        await runner.stop()


@pytest.mark.anyio
async def test_each_pipeline_gets_unique_correlation_id():
    """AC4: cada pipeline tem correlation_id unico."""
    correlation_ids = []

    async def track_execute(alert, bound_log):
        correlation_ids.append(alert.correlation_id)

    runner = PipelineRunner(max_concurrent=3, max_queued=10)
    runner._execute_pipeline = track_execute
    await runner.start()
    try:
        await runner.submit(_make_alert("inc-a"))
        await runner.submit(_make_alert("inc-b"))
        await runner.submit(_make_alert("inc-c"))
        await asyncio.sleep(0.05)
        assert len(set(correlation_ids)) == 3
    finally:
        await runner.stop()
