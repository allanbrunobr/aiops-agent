"""B8: validação de telemetria na borda de ingestão (NaN/inf/campos ausentes)."""

from __future__ import annotations

import io
import json
import math
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from data.models import MetricPoint
from interfaces.cli.data_sources import (
    drop_invalid_metric_points,
    is_valid_metric_point,
    step_fetch_databricks_data,
)
from main import main

pytestmark = pytest.mark.unit

T0 = datetime(2026, 1, 1, 12, 0, 0)


def _mp(value: object = 1.0, **kw: object) -> MetricPoint:
    base: dict = {
        "timestamp": T0,
        "service": "svc",
        "metric_name": "cpu",
        "value": value,
    }
    base.update(kw)
    return MetricPoint(**base)


@pytest.mark.parametrize(
    "point",
    [
        _mp(float("nan")),
        _mp(float("inf")),
        _mp(float("-inf")),
        _mp(None),
        _mp("abc"),
        _mp(True),
        _mp(service=""),
        _mp(metric_name=""),
        _mp(timestamp=None),
    ],
    ids=["nan", "inf", "-inf", "none", "str", "bool", "no-svc", "no-metric", "no-ts"],
)
def test_invalid_points_rejected(point: MetricPoint) -> None:
    assert not is_valid_metric_point(point)


def test_valid_points_accepted() -> None:
    assert is_valid_metric_point(_mp(0.0))
    assert is_valid_metric_point(_mp(3))


def test_drop_invalid_metric_points_counts_and_filters() -> None:
    inc = MagicMock()
    inc.metrics = [_mp(1.0), _mp(float("nan")), _mp(float("inf")), _mp(2.0)]
    assert drop_invalid_metric_points(inc) == 2
    assert [m.value for m in inc.metrics] == [1.0, 2.0]
    assert drop_invalid_metric_points(inc) == 0


def _src(raw_metrics: list[dict]) -> tuple[MagicMock, MagicMock, MagicMock]:
    logs, metrics, traces = MagicMock(), MagicMock(), MagicMock()
    logs.search.return_value = []
    traces.find_traces.return_value = []
    metrics.query.return_value = raw_metrics
    return logs, metrics, traces


def test_databricks_ingestion_drops_malformed_raw_payloads() -> None:
    ts = T0.isoformat()
    raw = [
        {"timestamp": ts, "service": "a", "metric": "cpu", "value": 1.5},
        {"timestamp": ts, "service": "a", "metric": "cpu", "value": float("nan")},
        {"timestamp": ts, "service": "a", "metric": "cpu", "value": float("inf")},
        {"timestamp": ts, "service": "a", "metric": "cpu"},  # value ausente
        {"timestamp": ts, "service": "a", "metric": "cpu", "value": None},
        {"timestamp": ts, "service": "a", "metric": "cpu", "value": "x"},
        {"timestamp": ts, "service": "a", "value": 1.0},  # metric ausente
        {"timestamp": "não-é-data", "service": "a", "metric": "m", "value": 1.0},
    ]
    stats: dict[str, int] = {}
    with redirect_stdout(io.StringIO()):
        incident, _ = step_fetch_databricks_data(*_src(raw), stats=stats)
    assert [m.value for m in incident.metrics] == [1.5]
    assert stats["telemetry_dropped"] == 7
    assert all(math.isfinite(m.value) for m in incident.metrics)


def test_pipeline_reports_telemetry_dropped(tmp_path: Path) -> None:
    import data.sample_incident as sample

    incident = sample.create_incident()
    good = len(incident.metrics)
    incident.metrics.extend(
        _mp(float("nan"), timestamp=T0 + timedelta(seconds=i)) for i in range(10)
    )
    argv = [
        "main.py",
        "--no-llm",
        "--no-rag",
        "--output-dir",
        str(tmp_path),
        "--output-format",
        "json",
    ]
    with (
        patch(
            "interfaces.cli.v0_pipeline.step_generate_data",
            return_value=(incident, "nan-injected"),
        ),
        patch("sys.argv", argv),
        patch("main.load_local_env"),
        redirect_stdout(io.StringIO()),
        redirect_stderr(io.StringIO()),
    ):
        code = main()
    assert code in {0, 2, 3}, f"exit inesperado {code}"
    assert len(incident.metrics) == good
    pm = sorted(tmp_path.glob("postmortem_*"))
    assert pm, "postmortem não gerado"
    meta = json.loads(pm[-1].read_text())["meta"]
    assert meta["telemetry_dropped"] == 10
