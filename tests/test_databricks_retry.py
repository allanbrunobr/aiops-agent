"""B7: 1 retry (backoff 2s) no conector Databricks, só para timeout.

Mock do SDK (WorkspaceClient) — nada de rede; time.sleep é patchado.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tools.databricks_connector import (
    DatabricksConnectorError,
    DatabricksLogSource,
)

_CREDS = {
    "host": "https://test.databricks.com",
    "token": "dapi_test_token",
    "warehouse_id": "test_warehouse_123",
}


def _ok_response():
    resp = MagicMock()
    resp.status.state.value = "SUCCEEDED"
    col = MagicMock()
    col.name = "a"
    resp.manifest.schema.columns = [col]
    resp.result.data_array = [["1"]]
    return resp


def _source(side_effect):
    source = DatabricksLogSource(**_CREDS)
    client = MagicMock()
    client.statement_execution.execute_statement.side_effect = side_effect
    source._client = client
    return source, client.statement_execution.execute_statement


@pytest.mark.unit
def test_timeout_then_success_retries_once_with_backoff():
    source, call = _source([TimeoutError("timed out"), _ok_response()])
    with patch("tools.databricks_connector.time.sleep") as sleep:
        rows = source._execute_query("SELECT 1")
    assert rows == [{"a": "1"}]
    assert call.call_count == 2
    sleep.assert_called_once_with(2.0)


@pytest.mark.unit
def test_two_timeouts_raise_connector_error_after_one_retry():
    source, call = _source([TimeoutError("timed out"), TimeoutError("timed out")])
    with patch("tools.databricks_connector.time.sleep"):
        with pytest.raises(DatabricksConnectorError) as exc:
            source._execute_query("SELECT 1")
    assert exc.value.error_type == "timeout"
    assert call.call_count == 2  # 1 tentativa + 1 retry, nunca mais


@pytest.mark.unit
@pytest.mark.parametrize(
    "error",
    [PermissionError("403 forbidden"), ValueError("syntax error near x")],
)
def test_non_timeout_errors_are_not_retried(error):
    source, call = _source([error, _ok_response()])
    with patch("tools.databricks_connector.time.sleep") as sleep:
        with pytest.raises(DatabricksConnectorError) as exc:
            source._execute_query("SELECT 1")
    assert exc.value.error_type != "timeout"
    assert call.call_count == 1
    sleep.assert_not_called()


@pytest.mark.unit
def test_failed_query_state_is_not_retried():
    resp = MagicMock()
    resp.status.state.value = "FAILED"
    resp.status.error.message = "boom"
    source, call = _source([resp, _ok_response()])
    with patch("tools.databricks_connector.time.sleep") as sleep:
        with pytest.raises(DatabricksConnectorError) as exc:
            source._execute_query("SELECT 1")
    assert exc.value.error_type == "query"
    assert call.call_count == 1
    sleep.assert_not_called()
