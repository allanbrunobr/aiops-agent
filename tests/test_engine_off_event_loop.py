"""A engine síncrona não roda dentro do event loop (A2A e MCP).

P2 — B6/4B-04 (docs/analise-20261109.md): `_handle_diagnose` (A2A) chamava
`engine.run` — síncrono, com chamadas de LLM — direto no handler async, e o
event loop inteiro parava: /health, outras tasks, MCP. Depois do P2-1 o
`diagnose_incident` do MCP passou a chamar a engine e tinha o mesmo defeito.

Instrumento: uma corrotina vizinha conta ticks de 20 ms enquanto a engine
"trabalha" 0,4 s. Com o loop travado ela fica parada; livre, conta vários.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import secrets
import time

import httpx
import pytest
from fastapi import FastAPI

from auth.jwt_validator import issue_token

WORK_SECONDS = 0.4
TICK = 0.02


class _SlowEngine:
    def run(self, pipeline, state):
        time.sleep(WORK_SECONDS)  # simula pipeline síncrono com LLM
        return dataclasses.replace(state, diagnosis={"root_cause": "pool exhausted"})


async def _ticks_while(coro) -> tuple[int, object]:
    ticks = 0
    done = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not done.is_set():
            await asyncio.sleep(TICK)
            ticks += 1

    task = asyncio.create_task(ticker())
    await asyncio.sleep(0)  # deixa o ticker começar
    try:
        result = await coro
    finally:
        done.set()
        await task
    return ticks, result


@pytest.mark.unit
class TestEngineRunsOffTheEventLoop:
    def test_a2a_diagnose_does_not_block_the_loop(self):
        from interfaces.http.routes.a2a import router

        key = secrets.token_urlsafe(48)
        app = FastAPI()
        app.include_router(router)
        app.state.a2a_jwt_secret = key
        app.state.engine = _SlowEngine()
        token = issue_token("assistant-agent", ["aiops:diagnose"], key)

        async def call():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                return await client.post("/a2a/tasks", json={
                    "jsonrpc": "2.0", "id": "1", "method": "tasks/send",
                    "params": {"id": "t1", "skill": {"id": "diagnose_incident"},
                               "message": {"role": "user", "parts": [{"type": "data", "data": {
                                   "source": "pagerduty", "severity": "high", "title": "t"}}]}},
                }, headers={"Authorization": f"Bearer {token}"})

        ticks, resp = asyncio.run(_ticks_while(call()))
        assert resp.status_code == 200
        assert resp.json()["result"]["status"]["state"] == "completed"
        # Livre: ~WORK_SECONDS/TICK = 20 ticks. Travado: 0–1.
        assert ticks >= 5, f"event loop travado durante engine.run ({ticks} ticks)"

    def test_mcp_diagnose_does_not_block_the_loop(self):
        from interfaces.mcp.tools import handle_diagnose_incident

        async def call():
            return await handle_diagnose_incident(
                {"source": "grafana", "severity": "critical", "title": "t",
                 "description": "d", "service": "payment-svc"},
                engine=_SlowEngine(), normalizer_registry=None, incident_store=None,
            )

        ticks, body = asyncio.run(_ticks_while(call()))
        assert json.loads(body)["diagnosis"]["root_cause"] == "pool exhausted"
        assert ticks >= 5, f"event loop travado durante engine.run ({ticks} ticks)"
