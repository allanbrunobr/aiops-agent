"""Demo: `incident.resolved` e o horário de início do incidente funcionam.

Achado do ruff (F823) ao adotar o lint: `_maybe_notify_slack` lia
`_incident_start_wall_time` e atribuía o mesmo nome sem `global`. O Python o
tratava como local: o ramo `incident.resolved` levantava UnboundLocalError e o
horário de início nunca chegava à variável do módulo.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


class _RecordingSlack:
    def __init__(self):
        self.events = []

    async def on_event(self, event):
        self.events.append(event)


@pytest.fixture
def demo(monkeypatch, isolated_cli):
    import interfaces.demo.server as server

    slack = _RecordingSlack()
    monkeypatch.setattr(server, "_slack", slack)
    monkeypatch.setattr(server, "_engine", SimpleNamespace(scenario_name="s"))
    monkeypatch.setattr(server, "_slack_fired", set())
    monkeypatch.setattr(server, "_incident_start_wall_time", 0.0)
    return server, slack


@pytest.mark.unit
class TestDemoIncidentLifecycle:
    def test_critical_records_incident_start_in_module_state(self, demo):
        server, _ = demo
        frame = {"risk_score": 85.0, "prediction": {"event_level": "prediction.alert"}}
        asyncio.run(server._maybe_notify_slack(frame))
        assert "critical" in server._slack_fired
        assert server._incident_start_wall_time > 0

    def test_resolved_after_critical_is_sent(self, demo):
        server, slack = demo
        server._slack_fired.add("critical")
        server._incident_start_wall_time = 1.0
        asyncio.run(server._maybe_notify_slack({"risk_score": 5.0, "prediction": None}))
        assert "resolved" in server._slack_fired
        assert any(e.event_type == "incident.resolved" for e in slack.events)
