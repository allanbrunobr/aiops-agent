"""Escalada do ContinuousMonitor (VB3): camada barata → profunda + aviso proativo.

Diagnóstico e Slack são injetados (mock). Nenhum teste toca infra, rede ou
``output/`` do repo.
"""

from __future__ import annotations

import asyncio
import csv
import dataclasses
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import data.hpa_saturation_scenario as hpa
from core.lifecycle import LifecycleEvent
from domain.incident_state import IncidentState
from interfaces.http.notifications.slack import build_monitor_slack_notifier
from pipeline.continuous_monitor import ContinuousMonitor, EscalationHook
from tools.csv_metrics_source import CsvMetricsSource

pytestmark = pytest.mark.unit

THRESHOLD = 0.9 * hpa.MEMORY_REQUEST_BYTES
T0 = datetime(2026, 11, 22, 8, 0)


class FakeDiagnose:
    """Pipeline de diagnóstico mock: registra chamadas, devolve proposta+postmortem."""

    def __init__(self) -> None:
        self.calls: list[IncidentState] = []

    def __call__(self, state: IncidentState) -> IncidentState:
        self.calls.append(state)
        return dataclasses.replace(
            state,
            diagnosis={"root_cause": "hpa_saturation", "confidence": 0.8},
            proposed_action={"action_type": "scale", "command": "kubectl scale ..."},
            postmortem_md="# Postmortem\nproposta aguardando aprovação humana",
        )


def _monitor(tmp_path: Path, hook: EscalationHook | None) -> ContinuousMonitor:
    csv_path = hpa.export_csv(tmp_path / "hpa.csv", seed=42)
    return ContinuousMonitor(
        CsvMetricsSource(csv_path),
        metrics=[hpa.METRIC_MEMORY],
        ewma_threshold=THRESHOLD,
        escalate_eta_minutes=60,
        escalation=hook,
    )


def _replay(mon: ContinuousMonitor, start: datetime, end: datetime) -> None:
    t = start
    while t <= end:
        mon.tick(t)
        t += timedelta(minutes=1)


def _hook(diag: FakeDiagnose, events: list[LifecycleEvent]) -> EscalationHook:
    return EscalationHook(diagnose=diag, notify=events.append, service="hpa-svc")


def test_escalates_once_when_warning_and_never_executes_infra(tmp_path: Path) -> None:
    diag: FakeDiagnose = FakeDiagnose()
    events: list[LifecycleEvent] = []
    mon = _monitor(tmp_path, _hook(diag, events))
    _replay(mon, hpa.START_TIME + timedelta(minutes=30), hpa.RESTART_AT)

    assert any(t.should_escalate for t in mon.history)
    assert len(diag.calls) == 1  # dedup: um incidente aberto = 1 diagnóstico
    assert len(mon.escalations) == 1
    res = mon.escalations[0]
    assert res.state.proposed_action and res.state.postmortem_md
    assert res.executed_infra is False
    assert res.requires_human_approval is True
    assert res.verification_mode == "simulated"
    st = diag.calls[0]
    assert st.alert.service == "hpa-svc" and st.alert.source == "continuous_monitor"
    assert st.metadata["monitor_window"]["end"] is not None


def test_slack_warning_received_with_eta(tmp_path: Path) -> None:
    diag: FakeDiagnose = FakeDiagnose()
    events: list[LifecycleEvent] = []
    mon = _monitor(tmp_path, _hook(diag, events))
    _replay(mon, hpa.START_TIME + timedelta(minutes=30), hpa.RESTART_AT)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type in {"prediction.warning", "prediction.alert"}
    assert ev.data["verification_mode"] == "simulated"
    assert ev.data["service"] == "hpa-svc"
    assert ev.data["eta_minutes"] is not None or ev.data["saturation"]


def test_flat_series_never_escalates(tmp_path: Path) -> None:
    path = tmp_path / "flat.csv"
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "service", "metric_name", "value"])
        for i in range(60):
            w.writerow([(T0 + timedelta(minutes=i)).isoformat(), "svc", "m", 50.0])
    diag: FakeDiagnose = FakeDiagnose()
    events: list[LifecycleEvent] = []
    mon = ContinuousMonitor(
        CsvMetricsSource(path), metrics=["m"], escalation=_hook(diag, events)
    )
    for i in range(10, 60):
        mon.tick(T0 + timedelta(minutes=i))
    assert diag.calls == [] and events == [] and mon.escalations == []


def test_hook_rearms_on_reset(tmp_path: Path) -> None:
    diag: FakeDiagnose = FakeDiagnose()
    events: list[LifecycleEvent] = []
    hook = _hook(diag, events)
    mon = _monitor(tmp_path, hook)
    _replay(mon, hpa.START_TIME + timedelta(minutes=30), hpa.RESTART_AT)
    assert len(diag.calls) == 1
    hook.reset()  # nível voltou ao normal → próximo ciclo pode escalar de novo
    first = next(t for t in mon.history if t.should_escalate)
    assert hook(first, (first.timestamp, first.timestamp)) is not None
    assert len(diag.calls) == 2
    assert hook(first, (first.timestamp, first.timestamp)) is None  # dedup


def test_diagnose_failure_does_not_kill_monitor(tmp_path: Path) -> None:
    def boom(state: IncidentState) -> IncidentState:
        raise RuntimeError("llm fora do ar")

    events: list[LifecycleEvent] = []
    hook = EscalationHook(diagnose=boom, notify=events.append, service="s")
    mon = _monitor(tmp_path, hook)
    _replay(mon, hpa.START_TIME + timedelta(minutes=30), hpa.RESTART_AT)
    assert mon.escalations and mon.escalations[0].error == "llm fora do ar"
    assert events  # o aviso proativo sai mesmo sem diagnóstico
    assert mon.escalations[0].executed_infra is False


def test_notify_failure_does_not_block_diagnosis(tmp_path: Path) -> None:
    diag = FakeDiagnose()

    def bad_notify(ev: LifecycleEvent) -> None:
        raise OSError("slack down")

    mon = _monitor(
        tmp_path, EscalationHook(diagnose=diag, notify=bad_notify, service="s")
    )
    _replay(mon, hpa.START_TIME + timedelta(minutes=30), hpa.RESTART_AT)
    assert len(diag.calls) == 1


def test_no_hook_keeps_vb2_behaviour(tmp_path: Path) -> None:
    mon = _monitor(tmp_path, None)
    _replay(mon, hpa.START_TIME + timedelta(minutes=30), hpa.RESTART_AT)
    assert mon.escalations == []


def test_atick_runs_diagnosis_off_event_loop(tmp_path: Path) -> None:
    main_thread = threading.get_ident()
    seen: list[int] = []

    def diag(state: IncidentState) -> IncidentState:
        seen.append(threading.get_ident())
        return state

    mon = _monitor(tmp_path, EscalationHook(diagnose=diag, service="s"))

    async def drive() -> None:
        t = hpa.START_TIME + timedelta(minutes=30)
        while t <= hpa.RESTART_AT and not seen:
            await mon.atick(t)
            t += timedelta(minutes=1)

    asyncio.run(drive())
    assert seen and seen[0] != main_thread


def test_slack_notifier_is_dry_run_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIOPS_MONITOR_SLACK_REAL", raising=False)
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    monkeypatch.setenv("SLACK_CHANNEL", "#ops")
    assert build_monitor_slack_notifier()._dry_run is True


def test_slack_real_requires_flag_and_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIOPS_MONITOR_SLACK_REAL", "1")
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("SLACK_CHANNEL", "#ops")
    assert build_monitor_slack_notifier()._dry_run is True  # sem token
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake")
    assert build_monitor_slack_notifier()._dry_run is False


def test_dry_run_slack_prediction_is_fast_noop() -> None:
    notifier = build_monitor_slack_notifier()
    ev = LifecycleEvent(
        "prediction.warning",
        None,
        "inc-1",
        datetime(2026, 1, 1),
        {"event_level": "prediction.warning", "risk_score": 40.0, "eta_minutes": 12.0},
    )
    t0 = time.monotonic()
    asyncio.run(notifier.on_event(ev))
    assert time.monotonic() - t0 < 0.5  # sem retries/sleep com cliente None


def test_blueprint_diagnose_real_engine_proposes_without_executing(
    tmp_path: Path,
) -> None:
    """Camada profunda real (V1, no_llm): proposta/postmortem, sem execução de infra."""
    from app.monitor_setup import make_blueprint_diagnose
    from config.loader import load_config
    from data.sample_incident import create_incident

    cfg = load_config(None)
    inc = create_incident()
    events: list[LifecycleEvent] = []
    hook = EscalationHook(
        make_blueprint_diagnose(cfg), notify=events.append, service="payment-svc"
    )
    mon = _monitor(tmp_path, hook)
    # injeta os dados brutos do cenário no estado montado pelo hook
    orig = hook._build_state

    def with_data(tick, window):  # type: ignore[no-untyped-def]
        st = orig(tick, window)
        return dataclasses.replace(
            st,
            metadata={
                **st.metadata,
                "raw_incident_data": {
                    "logs": inc.logs,
                    "metrics": inc.metrics,
                    "traces": inc.traces,
                },
            },
        )

    hook._build_state = with_data  # type: ignore[method-assign]
    _replay(mon, hpa.START_TIME + timedelta(minutes=30), hpa.RESTART_AT)
    res = mon.escalations[0]
    assert res.executed_infra is False and res.requires_human_approval is True
    assert res.error is None and res.state.postmortem_md
