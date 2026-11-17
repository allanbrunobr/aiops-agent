"""F3-3: Grafana webhook → pipeline REAL → postmortem → Slack (isolado).

Sem Docker/servidores externos: app FastAPI via ASGITransport, engine real
em modo determinístico, notifier Slack fake em processo. Antes do fix,
_execute_pipeline era stub (só logava pipeline.stub_executed) e nada
chegava ao Slack.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

FIXTURE = (
    Path(__file__).parent.parent.parent
    / "infra"
    / "grafana_webhook_payload_example.json"
)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_grafana_webhook_runs_pipeline_and_notifies_slack(monkeypatch):
    monkeypatch.setenv("GRAFANA_WEBHOOK_TOKEN", "test-token-f33")

    from adapters.persistence.in_memory_store import InMemoryStore
    from app.builders import build_engine
    from infra.run_webhook_server import create_webhook_app
    from interfaces.http.webhook.pipeline_runner import PipelineRunner

    app = create_webhook_app()
    config = app.state.config
    store = InMemoryStore()
    events = []

    class FakeNotifier:
        async def on_event(self, event):
            events.append(event)

    runner = PipelineRunner(
        max_concurrent=2,
        max_queued=5,
        store=store,
        engine=build_engine(config, store=store),
        config=config,
        notifier=FakeNotifier(),
    )
    app.state.pipeline_runner = runner

    payload = json.loads(FIXTURE.read_text())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/webhook",
            json=payload,
            headers={"Authorization": "Bearer test-token-f33"},
        )

    assert resp.status_code == 202, resp.text
    cid = resp.json()["correlation_id"]

    # Aguarda o pipeline assíncrono concluir (evento final ao notifier)
    for _ in range(200):
        if any(e.event_type == "pipeline.completed" for e in events):
            break
        await asyncio.sleep(0.05)

    types = [e.event_type for e in events]
    assert "pipeline.completed" in types, f"pipeline não executou: {types}"

    final = store.get(cid)
    assert final is not None
    assert final.diagnosis and final.diagnosis.get("root_cause")
    assert final.postmortem_md, "postmortem não gerado"

    completed = next(e for e in events if e.event_type == "pipeline.completed")
    assert completed.data.get("root_cause")
    assert completed.data.get("postmortem")
    assert "payment-svc" in json.dumps(completed.data).lower()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_webhook_without_auth_rejected(monkeypatch):
    monkeypatch.setenv("GRAFANA_WEBHOOK_TOKEN", "test-token-f33")

    from infra.run_webhook_server import create_webhook_app

    app = create_webhook_app()
    payload = json.loads(FIXTURE.read_text())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/webhook", json=payload)

    assert resp.status_code == 401
