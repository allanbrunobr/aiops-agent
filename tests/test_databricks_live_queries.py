"""Modo databricks-live coleta de verdade (consultas corretas) e avisa quando vem vazio.

P2 — B8/4B-05 (docs/analise-20261109.md): main.step_fetch_databricks_data
chamava os conectores com valores que esvaziavam tudo sem erro:
- métricas: query(start=None, end=None) → None.isoformat() → exceção engolida;
- logs: search(query="*") → LIKE '%*%' (asterisco literal);
- traces: find_traces(service="") → WHERE service = ''.
O host/token abaixo são fictícios: _execute_query é substituído, nada sai.
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tools.databricks_connector import (
    DatabricksLogSource,
    DatabricksMetricsSource,
    DatabricksTraceSource,
)

FAKE = {"host": "https://example.invalid", "token": "placeholder", "warehouse_id": "w"}


def _capture(source):
    sqls: list[str] = []

    def execute(sql):
        sqls.append(sql)
        return []

    return sqls, patch.object(type(source), "_execute_query", lambda self, sql: execute(sql))


@pytest.mark.unit
class TestConnectorQueries:
    def test_log_wildcard_and_empty_service_are_not_literal_filters(self):
        src = DatabricksLogSource(**FAKE)
        sqls, p = _capture(src)
        with p:
            src.search(query="*", service="", start=None, end=None)
        assert "LIKE '%*%'" not in sqls[0]
        assert "= ''" not in sqls[0]

    def test_trace_empty_service_is_not_a_filter(self):
        src = DatabricksTraceSource(**FAKE)
        sqls, p = _capture(src)
        with p:
            src.find_traces(service="", min_duration_ms=0.0)
        assert "= ''" not in sqls[0]

    def test_metrics_without_window_does_not_fail(self):
        src = DatabricksMetricsSource(**FAKE)
        sqls, p = _capture(src)
        with p:
            assert src.query(promql="", start=None, end=None) == []
        assert "None" not in sqls[0]

    def test_metrics_window_is_still_applied(self):
        src = DatabricksMetricsSource(**FAKE)
        sqls, p = _capture(src)
        start, end = datetime(2026, 11, 1, 10), datetime(2026, 11, 1, 11)
        with p:
            src.query(promql="latency", start=start, end=end)
        assert start.isoformat() in sqls[0] and end.isoformat() in sqls[0]


@pytest.mark.unit
class TestFetchStep:
    def _sources(self, logs=(), metrics=(), traces=()):
        log_source, metrics_source, trace_source = MagicMock(), MagicMock(), MagicMock()
        log_source.search.return_value = list(logs)
        metrics_source.query.return_value = list(metrics)
        trace_source.find_traces.return_value = list(traces)
        return log_source, metrics_source, trace_source

    def test_fetch_uses_a_real_time_window(self):
        from main import step_fetch_databricks_data

        sources = self._sources(logs=[{"message": "x", "level": "ERROR", "service": "a"}])
        with redirect_stdout(io.StringIO()):
            step_fetch_databricks_data(*sources)
        for call in (sources[0].search.call_args, sources[1].query.call_args):
            start, end = call.kwargs["start"], call.kwargs["end"]
            assert isinstance(start, datetime) and isinstance(end, datetime)
            assert end - start == timedelta(minutes=60)

    def test_all_sources_empty_is_reported_loudly(self):
        from main import step_fetch_databricks_data

        out = io.StringIO()
        with redirect_stdout(out), patch("main._persist_log") as log:
            incident, _ = step_fetch_databricks_data(*self._sources())
        assert incident is not None  # contrato de fallback gracioso mantido
        assert "no telemetry" in out.getvalue().lower()
        assert log.warning.called
