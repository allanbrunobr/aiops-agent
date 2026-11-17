"""Regressão F3-2: engine.run síncrono não pode bloquear o event loop do A2A.

_handle_diagnose chamava engine.run() (bloqueante, até pipeline_timeout_seconds)
diretamente na rota async — qualquer request concorrente (ex.: health) ficava
preso até o diagnóstico terminar. Fix: asyncio.to_thread.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from config.loader import AppConfig
from domain.alert import NormalizedAlert
from domain.incident_state import IncidentState


def _make_state() -> IncidentState:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    alert = NormalizedAlert(
        source="a2a",
        source_id="s1",
        correlation_id="task-f32",
        title="Test",
        description="Concurrence test",
        severity="error",
        alert_type="A2ATask",
        timestamp=now,
        detection_timestamp=now,
    )
    return IncidentState(
        incident_id="task-f32",
        correlation_id="task-f32",
        alert=alert,
        received_at=now,
        started_at=now,
        diagnosis={"root_cause": "test", "evidence": [], "confidence": "medium"},
    )


class _SlowEngine:
    """Engine cujo run bloqueia a thread por 0.5s (simula pipeline pesado)."""

    def __init__(self):
        self.config = AppConfig()

    def run(self, pipeline, state):
        time.sleep(0.5)
        return _make_state()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a2a_diagnose_does_not_block_event_loop():
    from interfaces.http.routes import a2a

    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(engine=_SlowEngine(), config=AppConfig())
        )
    )

    started = time.monotonic()
    ticks: list[float] = []

    async def ticker():
        for _ in range(6):
            ticks.append(time.monotonic() - started)
            await asyncio.sleep(0.05)

    tick_task = asyncio.create_task(ticker())
    artifacts = await a2a._handle_diagnose(
        "task-f32",
        {"source": "a2a", "severity": "error", "title": "T", "description": "D"},
        request,
    )
    await tick_task

    assert artifacts, "diagnose deve retornar artifacts"
    # O ticker precisa ter progredido DURANTE o run bloqueante (0.5s).
    # Antes do fix, o run síncrono prendia o loop e todos os ticks vinham depois.
    assert any(t < 0.4 for t in ticks), f"event loop bloqueado: ticks={ticks}"
