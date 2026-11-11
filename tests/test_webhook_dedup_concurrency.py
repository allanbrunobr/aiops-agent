"""Dois envios simultâneos do mesmo alerta: um pipeline só.

O router consultava a dedup, fazia `await pipeline_runner.submit(...)` e só
então gravava o fingerprint. Quando o `submit` cede o event loop (lock de
admissão disputado), o segundo envio idêntico passava pela consulta antes do
registro do primeiro, e os dois abriam pipeline (pendência anotada na P2).
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from tests.test_webhook_router import PAYLOAD, TOKEN, _app

pytestmark = pytest.mark.unit

HEADERS = {"Alert-Source": "grafana", "Content-Type": "application/json",
           "Authorization": f"Bearer {TOKEN}"}


class _YieldingRunner:
    """Cede o event loop dentro do submit, como o lock de admissão disputado."""

    def __init__(self, answers=None):
        self.answers = list(answers or [])
        self.submitted = []

    async def submit(self, alert):
        await asyncio.sleep(0.01)
        accepted = self.answers.pop(0) if self.answers else True
        if accepted:
            self.submitted.append(alert)
        return accepted


async def _post_concurrently(app, n):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await asyncio.gather(*[
            client.post("/webhook", content=json.dumps(PAYLOAD), headers=HEADERS)
            for _ in range(n)
        ])


def test_simultaneous_identical_alerts_open_one_pipeline():
    runner = _YieldingRunner()
    responses = asyncio.run(_post_concurrently(_app(runner), 2))
    assert [r.status_code for r in responses] == [202, 202]
    assert len(runner.submitted) == 1
    assert sorted(bool(r.json().get("deduplicated")) for r in responses) == [False, True]


def test_rejected_alert_releases_its_reservation():
    """Recusa (fila cheia) não pode deixar o fingerprint reservado (4B-01)."""
    runner = _YieldingRunner([False, True])
    app = _app(runner)
    first, = asyncio.run(_post_concurrently(app, 1))
    assert first.status_code == 503
    assert app.state.dedup_store.size == 0
    retry, = asyncio.run(_post_concurrently(app, 1))
    assert retry.status_code == 202
    assert retry.json().get("deduplicated") is not True
    assert len(runner.submitted) == 1


def test_release_keeps_another_alerts_record():
    from interfaces.http.webhook.dedup import DedupStore

    store = DedupStore()
    store.record("fp", "cid-new", 60)
    store.release("fp", "cid-old")
    assert store.is_duplicate("fp") == (True, "cid-new")
