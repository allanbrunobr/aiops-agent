"""PipelineRunner: shutdown drena os pipelines aceitos (B14/4B-10).

P2 (docs/analise-20261109.md): stop() cancelava só o worker ocioso e
retornava; pipelines em voo e alertas na fila (já respondidos com 202) se
perdiam, e as tasks não tinham referência (podiam ser coletadas pelo GC).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from domain.alert import NormalizedAlert
from interfaces.http.webhook.pipeline_runner import PipelineRunner


def _alert(i: int) -> NormalizedAlert:
    now = datetime.now(timezone.utc)
    return NormalizedAlert(
        source="grafana", source_id=f"s{i}", correlation_id=f"inc-20261110-{i:08d}",
        title="t", description="d", severity="critical", alert_type="grafana.firing",
        timestamp=now, detection_timestamp=now,
    )


class _SlowRunner(PipelineRunner):
    def __init__(self, work_s: float, **kw):
        super().__init__(**kw)
        self.work_s = work_s
        self.done: list[str] = []

    async def _execute_pipeline(self, alert, bound_log):
        await asyncio.sleep(self.work_s)
        self.done.append(alert.correlation_id)


@pytest.mark.unit
class TestShutdown:
    def test_stop_waits_for_in_flight_and_queued_pipelines(self):
        async def scenario():
            runner = _SlowRunner(0.05, max_concurrent=1, max_queued=5)
            await runner.start()
            assert await runner.submit(_alert(1))  # em voo
            assert await runner.submit(_alert(2))  # na fila
            await runner.stop()
            return runner.done

        assert asyncio.run(scenario()) == ["inc-20261110-00000001", "inc-20261110-00000002"]

    def test_submit_is_refused_once_stopping(self):
        async def scenario():
            runner = _SlowRunner(0.05, max_concurrent=1, max_queued=5)
            await runner.start()
            await runner.submit(_alert(1))
            stopping = asyncio.create_task(runner.stop())
            await asyncio.sleep(0)
            refused = not await runner.submit(_alert(2))
            await stopping
            return refused

        assert asyncio.run(scenario()) is True

    def test_tasks_are_strongly_referenced_while_running(self):
        async def scenario():
            runner = _SlowRunner(0.05, max_concurrent=2, max_queued=5)
            await runner.start()
            await runner.submit(_alert(1))
            held = len(runner._tasks)
            await runner.stop()
            return held, len(runner._tasks)

        assert asyncio.run(scenario()) == (1, 0)

    def test_drain_timeout_cancels_what_is_left(self):
        async def scenario():
            runner = _SlowRunner(10.0, max_concurrent=1, max_queued=5)
            await runner.start()
            await runner.submit(_alert(1))
            loop = asyncio.get_running_loop()
            t0 = loop.time()
            await runner.stop(drain_timeout=0.05)
            return loop.time() - t0, runner.done

        elapsed, done = asyncio.run(scenario())
        assert elapsed < 2.0
        assert done == []
