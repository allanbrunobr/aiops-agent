"""Regressão F4-4: SQL parametrizado no conector Databricks.

Antes do fix: valores do usuário interpolados por f-string com escape manual
de apóstrofo (query.replace("'", "''")) — LIKE, LIMIT, serviços, trace_id.
Agora: marcadores nomeados (:name) + parameters da Statement Execution API;
nenhum valor do usuário aparece no texto SQL.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from tools.databricks_connector import (
    DatabricksLogSource,
    DatabricksMetricsSource,
    DatabricksTraceSource,
)

_CREDS = {
    "host": "https://test.databricks.com",
    "token": "dapi_test_token",
    "warehouse_id": "test_warehouse_123",
}

_INJECTION = "x' OR '1'='1"


def _capture(source):
    calls = []

    def mock_execute(sql, parameters=None):
        calls.append((sql, list(parameters or [])))
        return []

    return calls, patch.object(source, "_execute_query", side_effect=mock_execute)


def _param_values(params):
    return {name: value for name, value in params}


@pytest.mark.unit
def test_search_injection_is_parameterized():
    source = DatabricksLogSource(**_CREDS)
    calls, patcher = _capture(source)
    with patcher:
        source.search(query=_INJECTION, service="svc'; DROP TABLE logs;--", limit=50)

    sql, params = calls[0]
    assert ":query_pattern" in sql
    assert ":service" in sql
    assert ":limit" in sql
    assert _INJECTION not in sql
    assert "DROP TABLE" not in sql
    assert "''" not in sql  # sem escape manual
    values = _param_values(params)
    assert values["query_pattern"] == "%" + _INJECTION + "%"
    assert values["service"] == "svc'; DROP TABLE logs;--"
    assert values["limit"] == 50


@pytest.mark.unit
def test_search_timestamps_parameterized():
    source = DatabricksLogSource(**_CREDS)
    calls, patcher = _capture(source)
    start, end = datetime(2026, 3, 4, 14, 0), datetime(2026, 3, 4, 15, 0)
    with patcher:
        source.search(query="timeout", start=start, end=end)

    sql, params = calls[0]
    assert "2026-03-04T14:00:00" not in sql
    values = _param_values(params)
    assert values["start_ts"] == start
    assert values["end_ts"] == end


@pytest.mark.unit
def test_tail_service_parameterized():
    source = DatabricksLogSource(**_CREDS)
    calls, patcher = _capture(source)
    with patcher:
        source.tail(service=_INJECTION, lines=10)

    sql, params = calls[0]
    assert _INJECTION not in sql
    assert ":service" in sql and ":lines" in sql
    values = _param_values(params)
    assert values["service"] == _INJECTION
    assert values["lines"] == 10


@pytest.mark.unit
def test_metrics_query_parameterized():
    source = DatabricksMetricsSource(**_CREDS)
    calls, patcher = _capture(source)
    with patcher:
        source.query(
            promql=_INJECTION,
            start=datetime(2026, 3, 4, 14, 0),
            end=datetime(2026, 3, 4, 15, 0),
        )

    sql, params = calls[0]
    assert _INJECTION not in sql
    assert "2026-03-04T14:00:00" not in sql
    values = _param_values(params)
    assert any(v == "%" + _INJECTION + "%" for v in values.values())


@pytest.mark.unit
def test_find_traces_parameterized():
    source = DatabricksTraceSource(**_CREDS)
    calls, patcher = _capture(source)
    with patcher:
        source.find_traces(service=_INJECTION, min_duration_ms=100.0, limit=5)

    sql, params = calls[0]
    assert _INJECTION not in sql
    values = _param_values(params)
    assert values["service"] == _INJECTION
    assert values["min_duration"] == 100.0
    assert values["limit"] == 5


@pytest.mark.unit
def test_get_trace_parameterized():
    source = DatabricksTraceSource(**_CREDS)
    calls, patcher = _capture(source)
    with patcher:
        source.get_trace(trace_id=_INJECTION)

    sql, params = calls[0]
    assert _INJECTION not in sql
    assert ":trace_id" in sql
    assert _param_values(params)["trace_id"] == _INJECTION


@pytest.mark.unit
def test_execute_query_passes_sdk_parameters():
    source = DatabricksLogSource(**_CREDS)
    fake_client = MagicMock()
    response = MagicMock()
    response.status.state.value = "SUCCEEDED"
    response.manifest = None
    response.result = None
    fake_client.statement_execution.execute_statement.return_value = response

    with patch.object(source, "_get_client", return_value=fake_client):
        source._execute_query(
            "SELECT * FROM t WHERE a = :val LIMIT :n",
            [("val", _INJECTION), ("n", 7)],
        )

    kwargs = fake_client.statement_execution.execute_statement.call_args.kwargs
    sdk_params = kwargs["parameters"]
    assert sdk_params is not None
    by_name = {p.name: p for p in sdk_params}
    assert by_name["val"].value == _INJECTION
    assert by_name["val"].type == "STRING"
    assert by_name["n"].value == "7"
    assert by_name["n"].type == "INT"
