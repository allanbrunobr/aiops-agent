"""Testes do ContinuousMonitor (VB2): camada barata, determinística, zero LLM."""

from __future__ import annotations

import csv
import random
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import data.hpa_saturation_scenario as hpa
from config.loader import AppConfig
from pipeline.continuous_monitor import ContinuousMonitor, MonitorTick
from tools.csv_metrics_source import CsvMetricsSource

pytestmark = pytest.mark.unit

THRESHOLD = 0.9 * hpa.MEMORY_REQUEST_BYTES  # 90% do request (por pod)
T0 = datetime(2026, 11, 22, 8, 0)


def _hpa_monitor(tmp_path: Path) -> ContinuousMonitor:
    tmp_path.mkdir(parents=True, exist_ok=True)
    csv_path = hpa.export_csv(tmp_path / "hpa.csv", seed=42)
    return ContinuousMonitor(
        CsvMetricsSource(csv_path),
        metrics=[hpa.METRIC_MEMORY],
        ewma_threshold=THRESHOLD,
        escalate_eta_minutes=60,
    )


def _replay(
    mon: ContinuousMonitor, start: datetime, end: datetime
) -> list[MonitorTick]:
    out, t = [], start
    while t <= end:
        out.append(mon.tick(t))
        t += timedelta(minutes=1)
    return out


def _series_csv(path: Path, values: list[float], metric: str = "m") -> Path:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "service", "metric_name", "value"])
        for i, v in enumerate(values):
            w.writerow([(T0 + timedelta(minutes=i)).isoformat(), "svc", metric, v])
    return path


def test_hpa_replay_alerts_before_maxed_out(tmp_path: Path) -> None:
    mon = _hpa_monitor(tmp_path)
    ticks = _replay(mon, hpa.START_TIME + timedelta(minutes=30), hpa.RESTART_AT)
    first = next(t for t in ticks if t.level is not None)
    assert first.level in {"prediction.warning", "prediction.alert"}
    lead = (hpa.MAXED_OUT_AT - first.timestamp).total_seconds() / 60
    assert lead > 0, f"aviso só em {first.timestamp}, depois de maxed_out"
    assert first.should_escalate is True


def test_hpa_no_alert_in_normal_phase(tmp_path: Path) -> None:
    mon = _hpa_monitor(tmp_path)
    ticks = _replay(mon, hpa.START_TIME + timedelta(minutes=30), hpa.INCIDENT_START)
    assert all(t.level is None for t in ticks)
    assert not any(t.should_escalate for t in ticks)


@pytest.mark.parametrize("kind", ["flat", "noise"])
def test_flat_and_noise_never_alert(tmp_path: Path, kind: str) -> None:
    rng = random.Random(7)
    vals = [100.0 + (rng.gauss(0, 0.5) if kind == "noise" else 0.0) for _ in range(240)]
    mon = ContinuousMonitor(
        CsvMetricsSource(_series_csv(tmp_path / "s.csv", vals)), metrics=["m"]
    )
    ticks = _replay(mon, T0 + timedelta(minutes=10), T0 + timedelta(minutes=239))
    assert all(t.level is None and not t.should_escalate for t in ticks)
    assert all(t.saturation is None for t in ticks)


def test_tick_is_deterministic(tmp_path: Path) -> None:
    start, end = hpa.MAXED_OUT_AT - timedelta(minutes=120), hpa.MAXED_OUT_AT
    a = _replay(_hpa_monitor(tmp_path / "a"), start, end)
    b = _replay(_hpa_monitor(tmp_path / "b"), start, end)
    assert [(t.score, t.level, t.eta_minutes) for t in a] == [
        (t.score, t.level, t.eta_minutes) for t in b
    ]


def test_saturation_plateau_detected_and_escalates(tmp_path: Path) -> None:
    vals = [10.0 + i for i in range(40)] + [50.0] * 20  # sobe e trava no teto
    mon = ContinuousMonitor(
        CsvMetricsSource(_series_csv(tmp_path / "s.csv", vals)),
        metrics=["m"],
        ceilings={"m": 50.0},
    )
    ticks = _replay(mon, T0 + timedelta(minutes=30), T0 + timedelta(minutes=59))
    assert any(t.saturation is not None for t in ticks)
    assert any(t.level is not None and t.should_escalate for t in ticks)


def test_run_for_sleeps_between_ticks_only(tmp_path: Path) -> None:
    slept: list[float] = []
    now = iter(T0 + timedelta(minutes=i) for i in range(10))
    mon = ContinuousMonitor(
        CsvMetricsSource(_series_csv(tmp_path / "s.csv", [1.0] * 30)),
        interval_seconds=15,
        metrics=["m"],
        sleep=slept.append,
        clock=lambda: next(now),
    )
    out = mon.run_for(3)
    assert len(out) == 3 and slept == [15, 15]


def test_invalid_interval_rejected(tmp_path: Path) -> None:
    src = CsvMetricsSource(_series_csv(tmp_path / "s.csv", [1.0] * 6))
    with pytest.raises(ValueError):
        ContinuousMonitor(src, interval_seconds=0)


def test_monitor_config_defaults() -> None:
    cfg = AppConfig().monitor
    assert (cfg.interval_seconds, cfg.ewma_threshold, cfg.escalate_eta_minutes) == (
        60.0,
        0.90,
        30.0,
    )


def test_tick_path_imports_no_llm() -> None:
    code = (
        "import sys; import pipeline.continuous_monitor;"
        "bad=[m for m in sys.modules if m=='adapters.llm' or m.startswith('adapters.llm.')];"
        "print(bad); sys.exit(1 if bad else 0)"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True,
        env={"PYTHONPATH": "."},
    )
    assert r.returncode == 0, r.stdout + r.stderr
