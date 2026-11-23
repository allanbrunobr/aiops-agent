"""Testes dos adapters MetricsSource (VB1): Prometheus (HTTP real local) e CSV."""

from __future__ import annotations

import csv
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import data.hpa_saturation_scenario as hpa
from application.ports.telemetry_sources import MetricsSource
from tools.csv_metrics_source import CsvMetricsSource
from tools.prometheus_connector import (
    PrometheusConnectorError,
    PrometheusMetricsSource,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 22, 10, 0, tzinfo=timezone.utc)


class _Handler(BaseHTTPRequestHandler):
    """Servidor mock: roteia por self.server.script (lista de respostas)."""

    def do_GET(self) -> None:  # noqa: N802
        srv = self.server
        srv.requests.append(
            (urlparse(self.path).path, parse_qs(urlparse(self.path).query))
        )  # type: ignore[attr-defined]
        script = srv.script  # type: ignore[attr-defined]
        step = script.pop(0) if len(script) > 1 else script[0]
        status, body, delay = step
        if delay:
            import time

            time.sleep(delay)
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args: object) -> None:  # silencia stderr
        pass


@pytest.fixture
def prom_server():
    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    srv.requests = []  # type: ignore[attr-defined]
    srv.script = [(200, {}, 0)]  # type: ignore[attr-defined]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def _url(srv: HTTPServer) -> str:
    return f"http://127.0.0.1:{srv.server_address[1]}"


MATRIX = {
    "status": "success",
    "data": {
        "resultType": "matrix",
        "result": [
            {
                "metric": {"__name__": "mem", "service": "wbia"},
                "values": [[1790000000, "100.5"], [1790000060, "101"]],
            },
            {
                "metric": {"__name__": "mem", "pod": "wbia-2"},
                "values": [[1790000000, "NaN"], [1790000060, "7"]],
            },
        ],
    },
}


def _src(srv: HTTPServer, **kw) -> PrometheusMetricsSource:
    return PrometheusMetricsSource(
        base_url=_url(srv), timeout_seconds=1, backoff_seconds=0, **kw
    )


# ---------------------------- Prometheus ----------------------------------


def test_is_metrics_source_and_env_base_url(monkeypatch) -> None:
    monkeypatch.setenv("PROMETHEUS_URL", "http://prom.invalid:9090/")
    src = PrometheusMetricsSource()
    assert isinstance(src, MetricsSource)
    assert src.base_url == "http://prom.invalid:9090"


def test_missing_url_raises(monkeypatch) -> None:
    monkeypatch.delenv("PROMETHEUS_URL", raising=False)
    with pytest.raises(PrometheusConnectorError) as ei:
        PrometheusMetricsSource()
    assert ei.value.error_type == "config"


def test_query_range_params_and_parse(prom_server) -> None:
    prom_server.script = [(200, MATRIX, 0)]
    end = T0 + timedelta(hours=1)
    out = _src(prom_server).query("mem", T0, end, step_seconds=60)

    path, params = prom_server.requests[0]
    assert path == "/api/v1/query_range"
    assert params["query"] == ["mem"]
    assert params["step"] == ["60"]
    assert float(params["start"][0]) == T0.timestamp()
    assert float(params["end"][0]) == end.timestamp()

    # NaN descartado; 3 pontos válidos, formato do contrato
    assert len(out) == 3
    first = out[0]
    assert set(first) == {"timestamp", "service", "metric", "value"}
    assert first["metric"] == "mem"
    assert first["service"] == "wbia"
    assert first["value"] == 100.5
    assert datetime.fromisoformat(first["timestamp"]) == datetime.fromtimestamp(
        1790000000, timezone.utc
    )
    assert out[2]["service"] == "wbia-2"  # cai para label pod


def test_query_error_status_raises(prom_server) -> None:
    prom_server.script = [
        (400, {"status": "error", "errorType": "bad_data", "error": "parse"}, 0)
    ]
    with pytest.raises(PrometheusConnectorError) as ei:
        _src(prom_server).query("x{", T0, T0 + timedelta(minutes=1))
    assert ei.value.error_type == "http"
    assert len(prom_server.requests) == 1  # 4xx não faz retry


def test_retry_on_5xx_then_success(prom_server) -> None:
    prom_server.script = [(503, {}, 0), (503, {}, 0), (200, MATRIX, 0)]
    out = _src(prom_server, max_retries=2).query("mem", T0, T0 + timedelta(hours=1))
    assert len(prom_server.requests) == 3
    assert len(out) == 3


def test_retries_exhausted_raises(prom_server) -> None:
    prom_server.script = [(503, {}, 0)]
    with pytest.raises(PrometheusConnectorError) as ei:
        _src(prom_server, max_retries=2).query("mem", T0, T0 + timedelta(hours=1))
    assert len(prom_server.requests) == 3  # 1 + 2 retries
    assert ei.value.error_type == "http"


def test_timeout_is_retried_and_classified(prom_server) -> None:
    prom_server.script = [(200, MATRIX, 1.5)]  # mais lento que timeout=1s
    with pytest.raises(PrometheusConnectorError) as ei:
        _src(prom_server, max_retries=1).query("mem", T0, T0 + timedelta(hours=1))
    assert ei.value.error_type == "timeout"
    assert len(prom_server.requests) == 2


def test_connection_refused_classified() -> None:
    src = PrometheusMetricsSource(
        base_url="http://127.0.0.1:1",
        timeout_seconds=1,
        max_retries=1,
        backoff_seconds=0,
    )
    with pytest.raises(PrometheusConnectorError) as ei:
        src.query("mem", T0, T0 + timedelta(minutes=1))
    assert ei.value.error_type == "connection"


def test_get_alerts_active_only(prom_server) -> None:
    body = {
        "status": "success",
        "data": {
            "alerts": [
                {
                    "labels": {
                        "alertname": "KubeHpaMaxedOut",
                        "service": "wbia",
                        "severity": "warning",
                    },
                    "state": "firing",
                },
                {
                    "labels": {"alertname": "Pending1", "severity": "info"},
                    "state": "pending",
                },
            ]
        },
    }
    prom_server.script = [(200, body, 0)]
    src = _src(prom_server)
    active = src.get_alerts()
    assert prom_server.requests[0][0] == "/api/v1/alerts"
    assert active == [
        {
            "alert": "KubeHpaMaxedOut",
            "service": "wbia",
            "severity": "warning",
            "status": "firing",
        }
    ]
    assert len(src.get_alerts(active_only=False)) == 2


# ------------------------------- CSV --------------------------------------


@pytest.fixture
def scenario_csv(tmp_path: Path) -> Path:
    return hpa.export_csv(tmp_path / "hpa.csv")


def test_csv_is_metrics_source_and_roundtrip(scenario_csv: Path) -> None:
    src = CsvMetricsSource(scenario_csv)
    assert isinstance(src, MetricsSource)
    expected = [
        p
        for p in hpa.generate_metrics(42)
        if p.metric_name == "container_memory_working_set_bytes"
    ]
    out = src.query(
        "container_memory_working_set_bytes",
        expected[0].timestamp,
        expected[-1].timestamp,
    )
    assert len(out) == len(expected)  # sem truncar em 100
    assert out[0]["value"] == expected[0].value
    assert out[-1]["value"] == expected[-1].value
    assert {o["metric"] for o in out} == {"container_memory_working_set_bytes"}
    assert [o["timestamp"] for o in out] == sorted(o["timestamp"] for o in out)


def test_csv_window_filter_is_inclusive(scenario_csv: Path) -> None:
    pts = [
        p
        for p in hpa.generate_metrics(42)
        if p.metric_name == "container_memory_working_set_bytes"
    ]
    start, end = pts[10].timestamp, pts[19].timestamp
    out = CsvMetricsSource(scenario_csv).query(
        "container_memory_working_set_bytes", start, end
    )
    assert len(out) == 10
    assert out[0]["timestamp"] == pts[10].timestamp.isoformat()


def test_csv_promql_selector_and_no_match(scenario_csv: Path) -> None:
    pts = hpa.generate_metrics(42)
    lo, hi = pts[0].timestamp, pts[-1].timestamp
    src = CsvMetricsSource(scenario_csv)
    sel = src.query('container_memory_working_set_bytes{service="wbia"}', lo, hi)
    plain = src.query("container_memory_working_set_bytes", lo, hi)
    assert len(sel) == len(plain) > 0
    assert src.query("metrica_inexistente", lo, hi) == []


def test_csv_naive_and_aware_windows(tmp_path: Path) -> None:
    p = tmp_path / "m.csv"
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "service", "metric_name", "value"])
        w.writerow(["2026-11-22T10:00:00", "svc", "cpu", "1.5"])  # naive
        w.writerow(["2026-11-22T10:01:00+00:00", "svc", "cpu", "2.5"])  # aware
    src = CsvMetricsSource(p)
    out = src.query(
        "cpu",
        datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc),
        datetime(2026, 11, 22, 11, 0, tzinfo=timezone.utc),
    )
    assert [o["value"] for o in out] == [1.5, 2.5]


def test_csv_missing_columns_and_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    with pytest.raises(ValueError, match="timestamp"):
        CsvMetricsSource(bad)
    with pytest.raises(FileNotFoundError):
        CsvMetricsSource(tmp_path / "nao_existe.csv")


def test_csv_get_alerts_empty(scenario_csv: Path) -> None:
    assert CsvMetricsSource(scenario_csv).get_alerts() == []
