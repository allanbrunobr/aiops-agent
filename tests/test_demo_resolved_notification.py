"""Regressão: branch incident.resolved do demo não pode crashar o tick loop.

_maybe_notify_slack atribui _incident_start_wall_time no branch
prediction.critical sem declarar `global`, o que torna a variável local
em toda a função. A leitura no branch resolved (que executa antes)
levantava UnboundLocalError, derrubando o _tick_loop.
"""

import time
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolved_branch_reads_global_start_time(monkeypatch):
    from interfaces.demo import server

    engine = MagicMock()
    engine.scenario_name = "payment-pool-exhaustion"
    slack = AsyncMock()
    monkeypatch.setattr(server, "_engine", engine)
    monkeypatch.setattr(server, "_slack", slack)
    monkeypatch.setattr(server, "_slack_fired", {"critical"})
    monkeypatch.setattr(server, "_incident_start_wall_time", time.time() - 120.0)

    frame = {"risk_score": 5.0, "prediction": None}
    await server._maybe_notify_slack(frame)

    assert "resolved" in server._slack_fired
    slack.on_event.assert_awaited_once()
    event = slack.on_event.await_args.args[0]
    assert event.event_type == "incident.resolved"
    assert event.data["incident_duration_minutes"] == pytest.approx(2.0, abs=0.1)
