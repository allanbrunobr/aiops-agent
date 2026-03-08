"""Testes unitarios para o conector Databricks System Tables.

Valida DatabricksConnection (auth, config, timeout), DatabricksLogSource
(search, tail, mapeamento de colunas), DatabricksMetricsSource (query,
get_alerts, column mapping), DatabricksTraceSource (find_traces,
get_trace, column mapping) e tratamento de erros estruturado via
DatabricksConnectorError. Todos os testes usam mocks — nenhuma
chamada real ao Databricks.
"""
from __future__ import annotations

import sys
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from tools.databricks_connector import (
    DEFAULT_ALERTS_COLUMN_MAPPING,
    DEFAULT_COLUMN_MAPPING,
    DEFAULT_METRICS_COLUMN_MAPPING,
    DEFAULT_TRACES_COLUMN_MAPPING,
    DatabricksConnection,
    DatabricksConnectorError,
    DatabricksLogSource,
    DatabricksMetricsSource,
    DatabricksTraceSource,
)


# --- Helpers ---


def _make_log_source(**kwargs) -> DatabricksLogSource:
    """Cria DatabricksLogSource com config minima para testes."""
    defaults = {
        "host": "https://test.databricks.com",
        "token": "dapi_test_token",
        "warehouse_id": "test_warehouse_123",
    }
    defaults.update(kwargs)
    return DatabricksLogSource(**defaults)


def _make_connection(**kwargs) -> DatabricksConnection:
    """Cria DatabricksConnection com config minima para testes."""
    defaults = {
        "host": "https://test.databricks.com",
        "token": "dapi_test_token",
        "warehouse_id": "test_warehouse_123",
    }
    defaults.update(kwargs)
    return DatabricksConnection(**defaults)


def _sample_audit_rows() -> list[dict]:
    """Retorna linhas de exemplo simulando system.access.audit."""
    return [
        {
            "event_time": "2026-03-04T14:23:00",
            "action_name": "createCluster",
            "service_name": "clusters",
            "request_params": "{'cluster_name': 'prod-1'}",
            "source_ip_address": "10.0.0.1",
        },
        {
            "event_time": "2026-03-04T14:24:00",
            "action_name": "runCommand",
            "service_name": "sql",
            "request_params": "SELECT 1",
            "source_ip_address": "10.0.0.2",
        },
        {
            "event_time": "2026-03-04T14:25:00",
            "action_name": "deleteCluster",
            "service_name": "clusters",
            "request_params": "{'cluster_id': 'abc-123'}",
            "source_ip_address": "10.0.0.3",
        },
    ]


# --- Task 4.2: DatabricksConnection Construction Tests ---


class TestDatabricksConnectionConstruction:
    """Testes de construcao do DatabricksConnection."""

    @pytest.mark.unit
    def test_construction_with_explicit_params(self):
        """Construcao com parametros explicitos define host, token e warehouse."""
        conn = _make_connection()
        assert conn.host == "https://test.databricks.com"
        assert conn.token == "dapi_test_token"
        assert conn.warehouse_id == "test_warehouse_123"

    @pytest.mark.unit
    def test_construction_reads_env_vars(self, monkeypatch):
        """Construcao sem parametros le de variaveis de ambiente."""
        monkeypatch.setenv("DATABRICKS_HOST", "https://env.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_env_token")
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "env_warehouse")

        conn = DatabricksConnection()
        assert conn.host == "https://env.databricks.com"
        assert conn.token == "dapi_env_token"
        assert conn.warehouse_id == "env_warehouse"

    @pytest.mark.unit
    def test_construction_explicit_overrides_env(self, monkeypatch):
        """Parametros explicitos prevalecem sobre variaveis de ambiente."""
        monkeypatch.setenv("DATABRICKS_HOST", "https://env.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_env_token")
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "env_warehouse")

        conn = DatabricksConnection(
            host="https://explicit.databricks.com",
            token="dapi_explicit",
            warehouse_id="explicit_wh",
        )
        assert conn.host == "https://explicit.databricks.com"
        assert conn.token == "dapi_explicit"
        assert conn.warehouse_id == "explicit_wh"

    @pytest.mark.unit
    def test_missing_config_raises_error_with_list(self, monkeypatch):
        """Config ausente gera DatabricksConnectorError listando campos faltantes."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)

        with pytest.raises(DatabricksConnectorError) as exc_info:
            DatabricksConnection()

        err = exc_info.value
        assert err.error_type == "config"
        assert "host" in err.details.lower()
        assert "token" in err.details.lower()
        assert "warehouse_id" in err.details.lower()

    @pytest.mark.unit
    def test_missing_single_config_lists_only_missing(self, monkeypatch):
        """Apenas o campo faltante aparece na mensagem de erro."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)

        with pytest.raises(DatabricksConnectorError) as exc_info:
            DatabricksConnection(
                host="https://test.com",
                token="tok",
            )

        err = exc_info.value
        assert "warehouse_id" in err.details.lower()
        assert "host" not in err.details.lower() or "DATABRICKS_HOST" in err.details

    @pytest.mark.unit
    def test_timeout_defaults_to_30_seconds(self):
        """Timeout padrao e 30 segundos."""
        conn = _make_connection()
        assert conn.timeout_seconds == 30

    @pytest.mark.unit
    def test_timeout_custom_value(self):
        """Timeout customizado e respeitado."""
        conn = _make_connection(timeout_seconds=60)
        assert conn.timeout_seconds == 60


# --- Task 4.3: DatabricksLogSource.search() Tests ---


class TestDatabricksLogSourceSearch:
    """Testes do metodo search() do DatabricksLogSource."""

    @pytest.mark.unit
    def test_search_happy_path_returns_correct_format(self):
        """search() retorna list[dict] com campos timestamp, level, service, message."""
        source = _make_log_source()
        with patch.object(source, "_execute_query", return_value=_sample_audit_rows()):
            results = source.search("cluster")

        assert isinstance(results, list)
        assert len(results) == 3
        for entry in results:
            assert "timestamp" in entry
            assert "level" in entry
            assert "service" in entry
            assert "message" in entry

    @pytest.mark.unit
    def test_search_maps_columns_correctly(self):
        """search() aplica column_mapping do system.access.audit."""
        source = _make_log_source()
        rows = _sample_audit_rows()

        with patch.object(source, "_execute_query", return_value=rows):
            results = source.search("cluster")

        # Primeira linha: event_time -> timestamp, action_name -> level, etc.
        assert results[0]["timestamp"] == "2026-03-04T14:23:00"
        assert results[0]["level"] == "createCluster"
        assert results[0]["service"] == "clusters"
        assert results[0]["message"] == "{'cluster_name': 'prod-1'}"

    @pytest.mark.unit
    def test_search_sql_contains_where_clauses(self):
        """search() gera SQL com WHERE clauses para query, service e janela temporal."""
        source = _make_log_source()
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query", side_effect=mock_execute):
            source.search(
                query="timeout",
                service="clusters",
                start=datetime(2026, 3, 4, 14, 0),
                end=datetime(2026, 3, 4, 15, 0),
                limit=50,
            )

        sql = captured_sql[0]
        assert "timeout" in sql
        assert "clusters" in sql
        assert "2026-03-04T14:00:00" in sql
        assert "2026-03-04T15:00:00" in sql
        assert "LIMIT 50" in sql
        assert "ORDER BY" in sql

    @pytest.mark.unit
    def test_search_with_custom_column_mapping(self):
        """search() respeita column_mapping customizado."""
        custom_mapping = {
            "timestamp": "created_at",
            "level": "severity",
            "service": "origin",
            "message": "content",
        }
        source = _make_log_source(column_mapping=custom_mapping)
        rows = [
            {
                "created_at": "2026-03-04T14:00:00",
                "severity": "ERROR",
                "origin": "payment-svc",
                "content": "Connection refused",
            }
        ]

        with patch.object(source, "_execute_query", return_value=rows):
            results = source.search("Connection")

        assert results[0]["timestamp"] == "2026-03-04T14:00:00"
        assert results[0]["level"] == "ERROR"
        assert results[0]["service"] == "payment-svc"
        assert results[0]["message"] == "Connection refused"

    @pytest.mark.unit
    def test_search_empty_results(self):
        """search() retorna lista vazia quando nao ha resultados."""
        source = _make_log_source()

        with patch.object(source, "_execute_query", return_value=[]):
            results = source.search("nonexistent")

        assert results == []

    @pytest.mark.unit
    def test_search_default_table(self):
        """search() usa system.access.audit como tabela padrao."""
        source = _make_log_source()
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query", side_effect=mock_execute):
            source.search("test")

        assert "system.access.audit" in captured_sql[0]

    @pytest.mark.unit
    def test_search_custom_table(self):
        """search() respeita tabela customizada."""
        source = _make_log_source(table="system.billing.usage")
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query", side_effect=mock_execute):
            source.search("test")

        assert "system.billing.usage" in captured_sql[0]


# --- Task 4.4: DatabricksLogSource.tail() Tests ---


class TestDatabricksLogSourceTail:
    """Testes do metodo tail() do DatabricksLogSource."""

    @pytest.mark.unit
    def test_tail_happy_path_returns_correct_format(self):
        """tail() retorna list[dict] com campos timestamp, level, message (sem service)."""
        source = _make_log_source()

        with patch.object(source, "_execute_query", return_value=_sample_audit_rows()):
            results = source.tail("clusters")

        assert isinstance(results, list)
        assert len(results) == 3
        for entry in results:
            assert "timestamp" in entry
            assert "level" in entry
            assert "message" in entry
            # tail() nao inclui 'service' (conforme SimulatedLogSource)
            assert "service" not in entry

    @pytest.mark.unit
    def test_tail_sql_has_order_by_and_limit(self):
        """tail() gera SQL com ORDER BY timestamp DESC e LIMIT."""
        source = _make_log_source()
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query", side_effect=mock_execute):
            source.tail("clusters", lines=25)

        sql = captured_sql[0]
        assert "ORDER BY" in sql
        assert "DESC" in sql
        assert "LIMIT 25" in sql

    @pytest.mark.unit
    def test_tail_filters_by_service(self):
        """tail() filtra pela coluna de servico."""
        source = _make_log_source()
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query", side_effect=mock_execute):
            source.tail("sql")

        assert "sql" in captured_sql[0]

    @pytest.mark.unit
    def test_tail_default_lines_50(self):
        """tail() usa 50 como numero padrao de linhas."""
        source = _make_log_source()
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query", side_effect=mock_execute):
            source.tail("clusters")

        assert "LIMIT 50" in captured_sql[0]


# --- Task 4.5: Error Handling Tests ---


class TestErrorHandling:
    """Testes de tratamento de erros estruturado."""

    @pytest.mark.unit
    def test_connection_timeout_produces_timeout_error(self):
        """Timeout de conexao gera DatabricksConnectorError com error_type='timeout'."""
        source = _make_log_source()

        with patch.object(
            source,
            "_execute_query",
            side_effect=TimeoutError("Connection timed out"),
        ):
            with pytest.raises(DatabricksConnectorError) as exc_info:
                source.search("test")

        assert exc_info.value.error_type == "timeout"

    @pytest.mark.unit
    def test_connection_error_produces_timeout_error(self):
        """ConnectionError gera DatabricksConnectorError com error_type='timeout'."""
        source = _make_log_source()

        with patch.object(
            source,
            "_execute_query",
            side_effect=ConnectionError("Connection refused"),
        ):
            with pytest.raises(DatabricksConnectorError) as exc_info:
                source.search("test")

        assert exc_info.value.error_type == "timeout"

    @pytest.mark.unit
    def test_auth_failure_produces_auth_error(self):
        """Falha de autenticacao gera DatabricksConnectorError com error_type='auth'."""
        source = _make_log_source()

        with patch.object(
            source,
            "_execute_query",
            side_effect=PermissionError("Access denied"),
        ):
            with pytest.raises(DatabricksConnectorError) as exc_info:
                source.search("test")

        assert exc_info.value.error_type == "auth"

    @pytest.mark.unit
    def test_query_error_produces_query_error(self):
        """Erro de query gera DatabricksConnectorError com error_type='query'."""
        source = _make_log_source()

        with patch.object(
            source,
            "_execute_query",
            side_effect=ValueError("Invalid SQL"),
        ):
            with pytest.raises(DatabricksConnectorError) as exc_info:
                source.search("test")

        assert exc_info.value.error_type == "query"

    @pytest.mark.unit
    def test_missing_config_produces_config_error(self, monkeypatch):
        """Config ausente gera DatabricksConnectorError com error_type='config'."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)

        with pytest.raises(DatabricksConnectorError) as exc_info:
            DatabricksLogSource()

        assert exc_info.value.error_type == "config"

    @pytest.mark.unit
    def test_error_includes_service_name(self):
        """Erro inclui nome do servico (classe) para rastreabilidade."""
        source = _make_log_source()

        with patch.object(
            source,
            "_execute_query",
            side_effect=RuntimeError("Something broke"),
        ):
            with pytest.raises(DatabricksConnectorError) as exc_info:
                source.search("test")

        assert exc_info.value.service == "DatabricksLogSource"

    @pytest.mark.unit
    def test_error_includes_timestamp(self):
        """Erro inclui timestamp ISO para correlacao temporal."""
        source = _make_log_source()

        with patch.object(
            source,
            "_execute_query",
            side_effect=RuntimeError("fail"),
        ):
            with pytest.raises(DatabricksConnectorError) as exc_info:
                source.search("test")

        # Timestamp deve ser ISO parseable
        ts = exc_info.value.timestamp
        datetime.fromisoformat(ts)  # Raises se nao for valido

    @pytest.mark.unit
    def test_error_str_representation(self):
        """DatabricksConnectorError tem representacao textual legivel."""
        err = DatabricksConnectorError(
            service="TestService",
            error_type="timeout",
            timestamp="2026-03-04T14:23:00",
            details="Connection timed out after 30s",
        )
        text = str(err)
        assert "timeout" in text
        assert "TestService" in text
        assert "Connection timed out" in text

    @pytest.mark.unit
    def test_error_classification_401_message(self):
        """Mensagem com '401' classifica como auth error."""
        result = DatabricksConnection._classify_error(
            Exception("HTTP 401 Unauthorized")
        )
        assert result == "auth"

    @pytest.mark.unit
    def test_error_classification_generic(self):
        """Excecao generica classifica como query error."""
        result = DatabricksConnection._classify_error(
            Exception("Something unexpected")
        )
        assert result == "query"

    @pytest.mark.unit
    def test_tail_error_wraps_in_connector_error(self):
        """tail() tambem envolve erros em DatabricksConnectorError."""
        source = _make_log_source()

        with patch.object(
            source,
            "_execute_query",
            side_effect=RuntimeError("fail"),
        ):
            with pytest.raises(DatabricksConnectorError) as exc_info:
                source.tail("clusters")

        assert exc_info.value.error_type == "query"


# --- Task 4.6: Lazy Import Test ---


class TestLazyImport:
    """Testes de lazy-import do databricks.sdk."""

    @pytest.mark.unit
    def test_databricks_sdk_not_imported_at_module_level(self):
        """databricks.sdk nao e importado quando tools.databricks_connector e carregado."""
        # Recarregar para garantir estado limpo
        # Verificar que nenhum modulo databricks.sdk.* esta carregado
        # APOS importar nosso modulo
        sdk_modules = [
            m for m in sys.modules
            if m.startswith("databricks.sdk")
        ]
        # Pode ter sido importado por outro teste que chamou _get_client
        # O importante e que o import do modulo em si nao carrega o SDK
        # Testar via import fresco:
        import importlib
        # Limpar cache do SDK se existir
        sdk_keys = [k for k in sys.modules if k.startswith("databricks.sdk")]
        saved = {}
        for k in sdk_keys:
            saved[k] = sys.modules.pop(k)

        try:
            # Re-importar nosso modulo
            importlib.reload(
                sys.modules["tools.databricks_connector"]
            )
            # Verificar que SDK nao foi carregado
            sdk_after = [
                m for m in sys.modules if m.startswith("databricks.sdk")
            ]
            assert len(sdk_after) == 0, (
                f"databricks.sdk importado no nivel do modulo: {sdk_after}"
            )
        finally:
            # Restaurar modulos removidos
            sys.modules.update(saved)

    @pytest.mark.unit
    def test_get_client_triggers_sdk_import(self):
        """_get_client() importa databricks.sdk sob demanda."""
        source = _make_log_source()
        # Resetar cliente cacheado
        source._client = None

        mock_ws_class = MagicMock()
        mock_ws_instance = MagicMock()
        mock_ws_class.return_value = mock_ws_instance

        # Mockar o modulo databricks.sdk com WorkspaceClient
        mock_sdk = MagicMock()
        mock_sdk.WorkspaceClient = mock_ws_class

        with patch.dict("sys.modules", {"databricks.sdk": mock_sdk}):
            client = source._get_client()
            assert client is mock_ws_instance
            mock_ws_class.assert_called_once_with(
                host="https://test.databricks.com",
                token="dapi_test_token",
            )


# --- Task 4.7: LogSource ABC Compliance ---


class TestLogSourceCompliance:
    """Testes de conformidade com a interface LogSource ABC."""

    @pytest.mark.unit
    def test_is_subclass_of_log_source(self):
        """DatabricksLogSource e subclasse de LogSource."""
        from tools.interfaces import LogSource

        assert issubclass(DatabricksLogSource, LogSource)

    @pytest.mark.unit
    def test_search_signature_matches_abc(self):
        """search() tem a mesma assinatura da ABC LogSource."""
        import inspect

        from tools.interfaces import LogSource

        abc_sig = inspect.signature(LogSource.search)
        impl_sig = inspect.signature(DatabricksLogSource.search)

        # Parametros devem ser os mesmos
        abc_params = list(abc_sig.parameters.keys())
        impl_params = list(impl_sig.parameters.keys())
        assert abc_params == impl_params, (
            f"Assinatura diverge: ABC={abc_params}, impl={impl_params}"
        )

    @pytest.mark.unit
    def test_tail_signature_matches_abc(self):
        """tail() tem a mesma assinatura da ABC LogSource."""
        import inspect

        from tools.interfaces import LogSource

        abc_sig = inspect.signature(LogSource.tail)
        impl_sig = inspect.signature(DatabricksLogSource.tail)

        abc_params = list(abc_sig.parameters.keys())
        impl_params = list(impl_sig.parameters.keys())
        assert abc_params == impl_params, (
            f"Assinatura diverge: ABC={abc_params}, impl={impl_params}"
        )

    @pytest.mark.unit
    def test_search_returns_list_of_dicts(self):
        """search() retorna list[dict] conforme contrato ABC."""
        source = _make_log_source()

        with patch.object(source, "_execute_query", return_value=_sample_audit_rows()):
            results = source.search("test")

        assert isinstance(results, list)
        for item in results:
            assert isinstance(item, dict)

    @pytest.mark.unit
    def test_tail_returns_list_of_dicts(self):
        """tail() retorna list[dict] conforme contrato ABC."""
        source = _make_log_source()

        with patch.object(source, "_execute_query", return_value=_sample_audit_rows()):
            results = source.tail("clusters")

        assert isinstance(results, list)
        for item in results:
            assert isinstance(item, dict)

    @pytest.mark.unit
    def test_default_column_mapping_exists(self):
        """DEFAULT_COLUMN_MAPPING esta definido com campos esperados."""
        assert "timestamp" in DEFAULT_COLUMN_MAPPING
        assert "level" in DEFAULT_COLUMN_MAPPING
        assert "service" in DEFAULT_COLUMN_MAPPING
        assert "message" in DEFAULT_COLUMN_MAPPING


# =====================================================================
# Story 4.2: DatabricksMetricsSource + DatabricksTraceSource Tests
# =====================================================================


# --- Helpers para Story 4.2 ---


def _current_connector_error():
    """Retorna classe DatabricksConnectorError atual do modulo.

    Necessario porque TestLazyImport.test_databricks_sdk_not_imported_at_module_level
    faz importlib.reload() que cria novas classes. Apos o reload, as classes
    importadas no topo deste arquivo ficam desatualizadas (identidade de classe
    diferente). Este helper busca a classe atual do modulo para uso em
    pytest.raises() e instanciacao de erros.
    """
    import tools.databricks_connector

    return tools.databricks_connector.DatabricksConnectorError


def _make_metrics_source(**kwargs) -> DatabricksMetricsSource:
    """Cria DatabricksMetricsSource com config minima para testes."""
    defaults = {
        "host": "https://test.databricks.com",
        "token": "dapi_test_token",
        "warehouse_id": "test_warehouse_123",
    }
    defaults.update(kwargs)
    return DatabricksMetricsSource(**defaults)


def _make_trace_source(**kwargs) -> DatabricksTraceSource:
    """Cria DatabricksTraceSource com config minima para testes."""
    defaults = {
        "host": "https://test.databricks.com",
        "token": "dapi_test_token",
        "warehouse_id": "test_warehouse_123",
    }
    defaults.update(kwargs)
    return DatabricksTraceSource(**defaults)


def _sample_billing_rows() -> list[dict]:
    """Retorna linhas de exemplo simulando system.billing.usage."""
    return [
        {
            "usage_date": "2026-03-04",
            "sku_name": "STANDARD_ALL_PURPOSE_COMPUTE",
            "usage_unit": "DBU",
            "usage_quantity": "12.5",
            "account_id": "acc-001",
        },
        {
            "usage_date": "2026-03-04",
            "sku_name": "SERVERLESS_SQL",
            "usage_unit": "DBU",
            "usage_quantity": "8.3",
            "account_id": "acc-001",
        },
        {
            "usage_date": "2026-03-05",
            "sku_name": "STANDARD_ALL_PURPOSE_COMPUTE",
            "usage_unit": "DBU",
            "usage_quantity": "15.0",
            "account_id": "acc-001",
        },
    ]


def _sample_alerts_rows() -> list[dict]:
    """Retorna linhas de exemplo simulando tabela de alertas."""
    return [
        {
            "alert_name": "HighCPU",
            "service_name": "compute-pool",
            "severity": "critical",
            "status": "active",
        },
        {
            "alert_name": "DiskFull",
            "service_name": "storage",
            "severity": "warning",
            "status": "active",
        },
    ]


def _sample_query_history_rows() -> list[dict]:
    """Retorna linhas de exemplo simulando system.query.history."""
    return [
        {
            "query_id": "qry-001",
            "warehouse_id": "wh-prod-01",
            "statement_type": "SELECT",
            "query_start_time_ms": "2026-03-04T14:23:00",
            "duration": "4500",
            "status": "FINISHED",
        },
        {
            "query_id": "qry-002",
            "warehouse_id": "wh-prod-01",
            "statement_type": "INSERT",
            "query_start_time_ms": "2026-03-04T14:24:00",
            "duration": "1200",
            "status": "FINISHED",
        },
        {
            "query_id": "qry-003",
            "warehouse_id": "wh-dev-01",
            "statement_type": "SELECT",
            "query_start_time_ms": "2026-03-04T14:25:00",
            "duration": "300",
            "status": "FAILED",
        },
    ]


# --- Task 4: DatabricksMetricsSource Tests ---


class TestDatabricksMetricsSourceConstruction:
    """Testes de construcao do DatabricksMetricsSource."""

    @pytest.mark.unit
    def test_construction_with_explicit_params(self):
        """Construcao com parametros explicitos define host, token e warehouse."""
        source = _make_metrics_source()
        assert source.host == "https://test.databricks.com"
        assert source.token == "dapi_test_token"
        assert source.warehouse_id == "test_warehouse_123"

    @pytest.mark.unit
    def test_default_table(self):
        """Tabela padrao e system.billing.usage."""
        source = _make_metrics_source()
        assert source.table == "system.billing.usage"

    @pytest.mark.unit
    def test_custom_table(self):
        """Tabela customizada e respeitada."""
        source = _make_metrics_source(table="custom.metrics.table")
        assert source.table == "custom.metrics.table"

    @pytest.mark.unit
    def test_default_column_mapping(self):
        """Column mapping padrao usa DEFAULT_METRICS_COLUMN_MAPPING."""
        source = _make_metrics_source()
        assert source.column_mapping == DEFAULT_METRICS_COLUMN_MAPPING

    @pytest.mark.unit
    def test_custom_column_mapping(self):
        """Column mapping customizado e respeitado."""
        custom = {"timestamp": "ts", "service": "svc",
                  "metric_name": "name", "value": "val"}
        source = _make_metrics_source(column_mapping=custom)
        assert source.column_mapping == custom

    @pytest.mark.unit
    def test_alerts_table_default_none(self):
        """Tabela de alertas padrao e None."""
        source = _make_metrics_source()
        assert source.alerts_table is None

    @pytest.mark.unit
    def test_alerts_table_custom(self):
        """Tabela de alertas customizada e respeitada."""
        source = _make_metrics_source(alerts_table="custom.alerts")
        assert source.alerts_table == "custom.alerts"


class TestDatabricksMetricsSourceQuery:
    """Testes do metodo query() do DatabricksMetricsSource."""

    @pytest.mark.unit
    def test_query_happy_path_returns_correct_format(self):
        """query() retorna list[dict] com campos timestamp, service, metric, value."""
        source = _make_metrics_source()
        with patch.object(source, "_execute_query",
                          return_value=_sample_billing_rows()):
            results = source.query(
                "COMPUTE", datetime(2026, 3, 4), datetime(2026, 3, 5)
            )

        assert isinstance(results, list)
        assert len(results) == 3
        for entry in results:
            assert "timestamp" in entry
            assert "service" in entry
            assert "metric" in entry
            assert "value" in entry

    @pytest.mark.unit
    def test_query_maps_columns_correctly(self):
        """query() aplica column_mapping do system.billing.usage."""
        source = _make_metrics_source()
        rows = _sample_billing_rows()

        with patch.object(source, "_execute_query", return_value=rows):
            results = source.query(
                "COMPUTE", datetime(2026, 3, 4), datetime(2026, 3, 5)
            )

        assert results[0]["timestamp"] == "2026-03-04"
        assert results[0]["service"] == "STANDARD_ALL_PURPOSE_COMPUTE"
        assert results[0]["metric"] == "DBU"
        assert results[0]["value"] == 12.5

    @pytest.mark.unit
    def test_query_sql_contains_where_clauses(self):
        """query() gera SQL com WHERE clauses para time range e LIKE filter."""
        source = _make_metrics_source()
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query",
                          side_effect=mock_execute):
            source.query(
                "cpu", datetime(2026, 3, 4, 14, 0),
                datetime(2026, 3, 4, 15, 0)
            )

        sql = captured_sql[0]
        assert "cpu" in sql
        assert "2026-03-04T14:00:00" in sql
        assert "2026-03-04T15:00:00" in sql
        assert "LIKE" in sql
        assert "ORDER BY" in sql
        assert "LIMIT 100" in sql

    @pytest.mark.unit
    def test_query_with_custom_column_mapping(self):
        """query() respeita column_mapping customizado."""
        custom_mapping = {
            "timestamp": "ts", "service": "svc",
            "metric_name": "name", "value": "val",
        }
        source = _make_metrics_source(column_mapping=custom_mapping)
        rows = [{"ts": "2026-03-04", "svc": "web", "name": "rps",
                 "val": "100.5"}]

        with patch.object(source, "_execute_query", return_value=rows):
            results = source.query(
                "rps", datetime(2026, 3, 4), datetime(2026, 3, 5)
            )

        assert results[0]["timestamp"] == "2026-03-04"
        assert results[0]["service"] == "web"
        assert results[0]["metric"] == "rps"
        assert results[0]["value"] == 100.5

    @pytest.mark.unit
    def test_query_empty_results(self):
        """query() retorna lista vazia quando nao ha resultados."""
        source = _make_metrics_source()

        with patch.object(source, "_execute_query", return_value=[]):
            results = source.query(
                "nonexistent", datetime(2026, 3, 4), datetime(2026, 3, 5)
            )

        assert results == []

    @pytest.mark.unit
    def test_query_value_conversion_handles_none(self):
        """query() converte None para 0.0 no campo value."""
        source = _make_metrics_source()
        rows = [{"usage_date": "2026-03-04", "sku_name": "svc",
                 "usage_unit": "DBU", "usage_quantity": None}]

        with patch.object(source, "_execute_query", return_value=rows):
            results = source.query(
                "svc", datetime(2026, 3, 4), datetime(2026, 3, 5)
            )

        assert results[0]["value"] == 0.0

    @pytest.mark.unit
    def test_query_value_conversion_handles_invalid(self):
        """query() converte valor invalido para 0.0."""
        source = _make_metrics_source()
        rows = [{"usage_date": "2026-03-04", "sku_name": "svc",
                 "usage_unit": "DBU", "usage_quantity": "not_a_number"}]

        with patch.object(source, "_execute_query", return_value=rows):
            results = source.query(
                "svc", datetime(2026, 3, 4), datetime(2026, 3, 5)
            )

        assert results[0]["value"] == 0.0


class TestDatabricksMetricsSourceAlerts:
    """Testes do metodo get_alerts() do DatabricksMetricsSource."""

    @pytest.mark.unit
    def test_get_alerts_no_table_returns_empty(self):
        """get_alerts() retorna lista vazia quando alerts_table e None."""
        source = _make_metrics_source()
        results = source.get_alerts()
        assert results == []

    @pytest.mark.unit
    def test_get_alerts_no_table_does_not_query(self):
        """get_alerts() nao executa query quando alerts_table e None."""
        source = _make_metrics_source()
        with patch.object(source, "_execute_query") as mock_exec:
            source.get_alerts()
            mock_exec.assert_not_called()

    @pytest.mark.unit
    def test_get_alerts_with_table_returns_correct_format(self):
        """get_alerts() retorna list[dict] com campos alert, service, severity."""
        source = _make_metrics_source(alerts_table="custom.alerts")

        with patch.object(source, "_execute_query",
                          return_value=_sample_alerts_rows()):
            results = source.get_alerts()

        assert isinstance(results, list)
        assert len(results) == 2
        for entry in results:
            assert "alert" in entry
            assert "service" in entry
            assert "severity" in entry

    @pytest.mark.unit
    def test_get_alerts_maps_columns_correctly(self):
        """get_alerts() aplica alerts_column_mapping."""
        source = _make_metrics_source(alerts_table="custom.alerts")
        rows = _sample_alerts_rows()

        with patch.object(source, "_execute_query", return_value=rows):
            results = source.get_alerts()

        assert results[0]["alert"] == "HighCPU"
        assert results[0]["service"] == "compute-pool"
        assert results[0]["severity"] == "critical"

    @pytest.mark.unit
    def test_get_alerts_sql_has_active_filter(self):
        """get_alerts(active_only=True) gera SQL com WHERE status='active'."""
        source = _make_metrics_source(alerts_table="custom.alerts")
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query",
                          side_effect=mock_execute):
            source.get_alerts(active_only=True)

        assert "active" in captured_sql[0].lower()

    @pytest.mark.unit
    def test_get_alerts_uses_custom_status_column(self):
        """get_alerts(active_only=True) usa status_col do alerts_column_mapping."""
        custom_mapping = {
            "alert": "alert_name",
            "service": "service_name",
            "severity": "severity",
            "status": "alert_state",
        }
        source = _make_metrics_source(
            alerts_table="custom.alerts",
            alerts_column_mapping=custom_mapping,
        )
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query",
                          side_effect=mock_execute):
            source.get_alerts(active_only=True)

        sql = captured_sql[0]
        assert "alert_state" in sql
        assert "WHERE alert_state = 'active'" in sql

    @pytest.mark.unit
    def test_get_alerts_without_active_filter(self):
        """get_alerts(active_only=False) gera SQL sem filtro de status."""
        source = _make_metrics_source(alerts_table="custom.alerts")
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query",
                          side_effect=mock_execute):
            source.get_alerts(active_only=False)

        sql = captured_sql[0]
        assert "WHERE" not in sql or "active" not in sql.lower()


class TestDatabricksMetricsSourceErrors:
    """Testes de tratamento de erros do DatabricksMetricsSource."""

    @pytest.mark.unit
    def test_query_timeout_produces_timeout_error(self):
        """Timeout na query gera DatabricksConnectorError com error_type='timeout'."""
        err_cls = _current_connector_error()
        source = _make_metrics_source()

        with patch.object(source, "_execute_query",
                          side_effect=TimeoutError("timed out")):
            with pytest.raises(err_cls) as exc_info:
                source.query("test", datetime(2026, 3, 4), datetime(2026, 3, 5))

        assert exc_info.value.error_type == "timeout"

    @pytest.mark.unit
    def test_query_auth_failure_produces_auth_error(self):
        """Falha de autenticacao na query gera error_type='auth'."""
        err_cls = _current_connector_error()
        source = _make_metrics_source()

        with patch.object(source, "_execute_query",
                          side_effect=PermissionError("forbidden")):
            with pytest.raises(err_cls) as exc_info:
                source.query("test", datetime(2026, 3, 4), datetime(2026, 3, 5))

        assert exc_info.value.error_type == "auth"

    @pytest.mark.unit
    def test_query_error_includes_service_name(self):
        """Erro na query inclui nome da classe para rastreabilidade."""
        err_cls = _current_connector_error()
        source = _make_metrics_source()

        with patch.object(source, "_execute_query",
                          side_effect=RuntimeError("fail")):
            with pytest.raises(err_cls) as exc_info:
                source.query("test", datetime(2026, 3, 4), datetime(2026, 3, 5))

        assert exc_info.value.service == "DatabricksMetricsSource"

    @pytest.mark.unit
    def test_get_alerts_error_wraps_in_connector_error(self):
        """get_alerts() envolve erros em DatabricksConnectorError."""
        err_cls = _current_connector_error()
        source = _make_metrics_source(alerts_table="custom.alerts")

        with patch.object(source, "_execute_query",
                          side_effect=RuntimeError("fail")):
            with pytest.raises(err_cls) as exc_info:
                source.get_alerts()

        assert exc_info.value.error_type == "query"

    @pytest.mark.unit
    def test_connector_error_reraise(self):
        """DatabricksConnectorError e re-raised sem wrapping."""
        err_cls = _current_connector_error()
        source = _make_metrics_source()
        original = err_cls(
            service="test", error_type="query",
            timestamp="2026-03-04", details="original error"
        )

        with patch.object(source, "_execute_query",
                          side_effect=original):
            with pytest.raises(err_cls) as exc_info:
                source.query("test", datetime(2026, 3, 4), datetime(2026, 3, 5))

        assert exc_info.value is original


# --- Task 5: DatabricksTraceSource Tests ---


class TestDatabricksTraceSourceConstruction:
    """Testes de construcao do DatabricksTraceSource."""

    @pytest.mark.unit
    def test_construction_with_explicit_params(self):
        """Construcao com parametros explicitos define host, token e warehouse."""
        source = _make_trace_source()
        assert source.host == "https://test.databricks.com"
        assert source.token == "dapi_test_token"
        assert source.warehouse_id == "test_warehouse_123"

    @pytest.mark.unit
    def test_default_table(self):
        """Tabela padrao e system.query.history."""
        source = _make_trace_source()
        assert source.table == "system.query.history"

    @pytest.mark.unit
    def test_custom_table(self):
        """Tabela customizada e respeitada."""
        source = _make_trace_source(table="custom.traces.table")
        assert source.table == "custom.traces.table"

    @pytest.mark.unit
    def test_default_column_mapping(self):
        """Column mapping padrao usa DEFAULT_TRACES_COLUMN_MAPPING."""
        source = _make_trace_source()
        assert source.column_mapping == DEFAULT_TRACES_COLUMN_MAPPING

    @pytest.mark.unit
    def test_custom_column_mapping(self):
        """Column mapping customizado e respeitado."""
        custom = {"trace_id": "id", "service": "svc", "operation": "op",
                  "start_time": "ts", "duration_ms": "dur", "status": "st"}
        source = _make_trace_source(column_mapping=custom)
        assert source.column_mapping == custom


class TestDatabricksTraceSourceFindTraces:
    """Testes do metodo find_traces() do DatabricksTraceSource."""

    @pytest.mark.unit
    def test_find_traces_happy_path_returns_correct_format(self):
        """find_traces() retorna list[dict] com campos esperados."""
        source = _make_trace_source()

        with patch.object(source, "_execute_query",
                          return_value=_sample_query_history_rows()):
            results = source.find_traces("wh-prod-01")

        assert isinstance(results, list)
        assert len(results) == 3
        for entry in results:
            assert "trace_id" in entry
            assert "service" in entry
            assert "operation" in entry
            assert "duration_ms" in entry
            assert "status" in entry
            assert "attributes" in entry

    @pytest.mark.unit
    def test_find_traces_maps_columns_correctly(self):
        """find_traces() aplica column_mapping do system.query.history."""
        source = _make_trace_source()
        rows = _sample_query_history_rows()

        with patch.object(source, "_execute_query", return_value=rows):
            results = source.find_traces("wh-prod-01")

        assert results[0]["trace_id"] == "qry-001"
        assert results[0]["service"] == "wh-prod-01"
        assert results[0]["operation"] == "SELECT"
        assert results[0]["duration_ms"] == 4500.0
        assert results[0]["status"] == "FINISHED"
        assert results[0]["attributes"] == {}

    @pytest.mark.unit
    def test_find_traces_sql_contains_where_clauses(self):
        """find_traces() gera SQL com WHERE clauses para service, duration, time."""
        source = _make_trace_source()
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query",
                          side_effect=mock_execute):
            source.find_traces(
                service="wh-prod-01",
                min_duration_ms=1000.0,
                start=datetime(2026, 3, 4, 14, 0),
                end=datetime(2026, 3, 4, 15, 0),
                limit=10,
            )

        sql = captured_sql[0]
        assert "wh-prod-01" in sql
        assert "1000.0" in sql
        assert "2026-03-04T14:00:00" in sql
        assert "2026-03-04T15:00:00" in sql
        assert "LIMIT 10" in sql
        assert "ORDER BY" in sql

    @pytest.mark.unit
    def test_find_traces_sql_without_optional_filters(self):
        """find_traces() gera SQL minimo quando filtros opcionais sao None."""
        source = _make_trace_source()
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query",
                          side_effect=mock_execute):
            source.find_traces("wh-prod-01")

        sql = captured_sql[0]
        assert "wh-prod-01" in sql
        assert "LIMIT 20" in sql  # default limit

    @pytest.mark.unit
    def test_find_traces_empty_results(self):
        """find_traces() retorna lista vazia quando nao ha resultados."""
        source = _make_trace_source()

        with patch.object(source, "_execute_query", return_value=[]):
            results = source.find_traces("nonexistent")

        assert results == []

    @pytest.mark.unit
    def test_find_traces_duration_conversion_handles_invalid(self):
        """find_traces() converte duracao invalida para 0.0."""
        source = _make_trace_source()
        rows = [{"query_id": "q1", "warehouse_id": "wh",
                 "statement_type": "SELECT", "query_start_time_ms": "2026-03-04",
                 "duration": "not_a_number", "status": "OK"}]

        with patch.object(source, "_execute_query", return_value=rows):
            results = source.find_traces("wh")

        assert results[0]["duration_ms"] == 0.0

    @pytest.mark.unit
    def test_find_traces_with_custom_column_mapping(self):
        """find_traces() respeita column_mapping customizado."""
        custom = {"trace_id": "id", "service": "svc", "operation": "op",
                  "start_time": "ts", "duration_ms": "dur", "status": "st"}
        source = _make_trace_source(column_mapping=custom)
        rows = [{"id": "t1", "svc": "web", "op": "GET",
                 "ts": "2026-03-04", "dur": "250", "st": "OK"}]

        with patch.object(source, "_execute_query", return_value=rows):
            results = source.find_traces("web")

        assert results[0]["trace_id"] == "t1"
        assert results[0]["service"] == "web"
        assert results[0]["operation"] == "GET"
        assert results[0]["duration_ms"] == 250.0
        assert results[0]["status"] == "OK"


class TestDatabricksTraceSourceGetTrace:
    """Testes do metodo get_trace() do DatabricksTraceSource."""

    @pytest.mark.unit
    def test_get_trace_happy_path_returns_correct_format(self):
        """get_trace() retorna dict com trace_id e spans."""
        source = _make_trace_source()
        rows = [_sample_query_history_rows()[0]]

        with patch.object(source, "_execute_query", return_value=rows):
            result = source.get_trace("qry-001")

        assert isinstance(result, dict)
        assert result["trace_id"] == "qry-001"
        assert isinstance(result["spans"], list)
        assert len(result["spans"]) == 1

    @pytest.mark.unit
    def test_get_trace_span_has_correct_fields(self):
        """get_trace() retorna spans com campos service, operation, duration_ms, status."""
        source = _make_trace_source()
        rows = [_sample_query_history_rows()[0]]

        with patch.object(source, "_execute_query", return_value=rows):
            result = source.get_trace("qry-001")

        span = result["spans"][0]
        assert span["service"] == "wh-prod-01"
        assert span["operation"] == "SELECT"
        assert span["duration_ms"] == 4500.0
        assert span["status"] == "FINISHED"

    @pytest.mark.unit
    def test_get_trace_no_match_returns_empty_spans(self):
        """get_trace() retorna dict com spans vazio quando nao encontra."""
        source = _make_trace_source()

        with patch.object(source, "_execute_query", return_value=[]):
            result = source.get_trace("nonexistent")

        assert result["trace_id"] == "nonexistent"
        assert result["spans"] == []

    @pytest.mark.unit
    def test_get_trace_sql_filters_by_trace_id(self):
        """get_trace() gera SQL com WHERE para trace_id."""
        source = _make_trace_source()
        captured_sql = []

        def mock_execute(sql):
            captured_sql.append(sql)
            return []

        with patch.object(source, "_execute_query",
                          side_effect=mock_execute):
            source.get_trace("qry-001")

        sql = captured_sql[0]
        assert "qry-001" in sql
        assert "LIMIT 1" in sql


class TestDatabricksTraceSourceErrors:
    """Testes de tratamento de erros do DatabricksTraceSource."""

    @pytest.mark.unit
    def test_find_traces_timeout_produces_timeout_error(self):
        """Timeout em find_traces gera DatabricksConnectorError com error_type='timeout'."""
        err_cls = _current_connector_error()
        source = _make_trace_source()

        with patch.object(source, "_execute_query",
                          side_effect=TimeoutError("timed out")):
            with pytest.raises(err_cls) as exc_info:
                source.find_traces("wh-prod-01")

        assert exc_info.value.error_type == "timeout"

    @pytest.mark.unit
    def test_find_traces_auth_failure_produces_auth_error(self):
        """Falha de autenticacao em find_traces gera error_type='auth'."""
        err_cls = _current_connector_error()
        source = _make_trace_source()

        with patch.object(source, "_execute_query",
                          side_effect=PermissionError("forbidden")):
            with pytest.raises(err_cls) as exc_info:
                source.find_traces("wh-prod-01")

        assert exc_info.value.error_type == "auth"

    @pytest.mark.unit
    def test_find_traces_error_includes_service_name(self):
        """Erro em find_traces inclui nome da classe."""
        err_cls = _current_connector_error()
        source = _make_trace_source()

        with patch.object(source, "_execute_query",
                          side_effect=RuntimeError("fail")):
            with pytest.raises(err_cls) as exc_info:
                source.find_traces("wh-prod-01")

        assert exc_info.value.service == "DatabricksTraceSource"

    @pytest.mark.unit
    def test_get_trace_error_wraps_in_connector_error(self):
        """get_trace() envolve erros em DatabricksConnectorError."""
        err_cls = _current_connector_error()
        source = _make_trace_source()

        with patch.object(source, "_execute_query",
                          side_effect=RuntimeError("fail")):
            with pytest.raises(err_cls) as exc_info:
                source.get_trace("qry-001")

        assert exc_info.value.error_type == "query"

    @pytest.mark.unit
    def test_connector_error_reraise(self):
        """DatabricksConnectorError e re-raised sem wrapping."""
        err_cls = _current_connector_error()
        source = _make_trace_source()
        original = err_cls(
            service="test", error_type="query",
            timestamp="2026-03-04", details="original error"
        )

        with patch.object(source, "_execute_query",
                          side_effect=original):
            with pytest.raises(err_cls) as exc_info:
                source.find_traces("wh-prod-01")

        assert exc_info.value is original


# --- ABC Compliance Tests ---


class TestMetricsSourceCompliance:
    """Testes de conformidade com a interface MetricsSource ABC."""

    @pytest.mark.unit
    def test_is_subclass_of_metrics_source(self):
        """DatabricksMetricsSource e subclasse de MetricsSource."""
        from tools.interfaces import MetricsSource

        assert issubclass(DatabricksMetricsSource, MetricsSource)

    @pytest.mark.unit
    def test_query_signature_matches_abc(self):
        """query() tem a mesma assinatura da ABC MetricsSource."""
        import inspect

        from tools.interfaces import MetricsSource

        abc_sig = inspect.signature(MetricsSource.query)
        impl_sig = inspect.signature(DatabricksMetricsSource.query)

        abc_params = list(abc_sig.parameters.keys())
        impl_params = list(impl_sig.parameters.keys())
        assert abc_params == impl_params, (
            f"Assinatura diverge: ABC={abc_params}, impl={impl_params}"
        )

    @pytest.mark.unit
    def test_get_alerts_signature_matches_abc(self):
        """get_alerts() tem a mesma assinatura da ABC MetricsSource."""
        import inspect

        from tools.interfaces import MetricsSource

        abc_sig = inspect.signature(MetricsSource.get_alerts)
        impl_sig = inspect.signature(DatabricksMetricsSource.get_alerts)

        abc_params = list(abc_sig.parameters.keys())
        impl_params = list(impl_sig.parameters.keys())
        assert abc_params == impl_params, (
            f"Assinatura diverge: ABC={abc_params}, impl={impl_params}"
        )

    @pytest.mark.unit
    def test_query_returns_list_of_dicts(self):
        """query() retorna list[dict] conforme contrato ABC."""
        source = _make_metrics_source()

        with patch.object(source, "_execute_query",
                          return_value=_sample_billing_rows()):
            results = source.query(
                "test", datetime(2026, 3, 4), datetime(2026, 3, 5)
            )

        assert isinstance(results, list)
        for item in results:
            assert isinstance(item, dict)

    @pytest.mark.unit
    def test_default_metrics_column_mapping_exists(self):
        """DEFAULT_METRICS_COLUMN_MAPPING esta definido com campos esperados."""
        assert "timestamp" in DEFAULT_METRICS_COLUMN_MAPPING
        assert "service" in DEFAULT_METRICS_COLUMN_MAPPING
        assert "metric_name" in DEFAULT_METRICS_COLUMN_MAPPING
        assert "value" in DEFAULT_METRICS_COLUMN_MAPPING


class TestTraceSourceCompliance:
    """Testes de conformidade com a interface TraceSource ABC."""

    @pytest.mark.unit
    def test_is_subclass_of_trace_source(self):
        """DatabricksTraceSource e subclasse de TraceSource."""
        from tools.interfaces import TraceSource

        assert issubclass(DatabricksTraceSource, TraceSource)

    @pytest.mark.unit
    def test_find_traces_signature_matches_abc(self):
        """find_traces() tem a mesma assinatura da ABC TraceSource."""
        import inspect

        from tools.interfaces import TraceSource

        abc_sig = inspect.signature(TraceSource.find_traces)
        impl_sig = inspect.signature(DatabricksTraceSource.find_traces)

        abc_params = list(abc_sig.parameters.keys())
        impl_params = list(impl_sig.parameters.keys())
        assert abc_params == impl_params, (
            f"Assinatura diverge: ABC={abc_params}, impl={impl_params}"
        )

    @pytest.mark.unit
    def test_get_trace_signature_matches_abc(self):
        """get_trace() tem a mesma assinatura da ABC TraceSource."""
        import inspect

        from tools.interfaces import TraceSource

        abc_sig = inspect.signature(TraceSource.get_trace)
        impl_sig = inspect.signature(DatabricksTraceSource.get_trace)

        abc_params = list(abc_sig.parameters.keys())
        impl_params = list(impl_sig.parameters.keys())
        assert abc_params == impl_params, (
            f"Assinatura diverge: ABC={abc_params}, impl={impl_params}"
        )

    @pytest.mark.unit
    def test_find_traces_returns_list_of_dicts(self):
        """find_traces() retorna list[dict] conforme contrato ABC."""
        source = _make_trace_source()

        with patch.object(source, "_execute_query",
                          return_value=_sample_query_history_rows()):
            results = source.find_traces("wh-prod-01")

        assert isinstance(results, list)
        for item in results:
            assert isinstance(item, dict)

    @pytest.mark.unit
    def test_default_traces_column_mapping_exists(self):
        """DEFAULT_TRACES_COLUMN_MAPPING esta definido com campos esperados."""
        assert "trace_id" in DEFAULT_TRACES_COLUMN_MAPPING
        assert "service" in DEFAULT_TRACES_COLUMN_MAPPING
        assert "operation" in DEFAULT_TRACES_COLUMN_MAPPING
        assert "start_time" in DEFAULT_TRACES_COLUMN_MAPPING
        assert "duration_ms" in DEFAULT_TRACES_COLUMN_MAPPING
        assert "status" in DEFAULT_TRACES_COLUMN_MAPPING


# --- Task 6: Shared Configuration Tests ---


class TestSharedConfiguration:
    """Testes de configuracao compartilhada entre os tres conectores."""

    @pytest.mark.unit
    def test_all_three_connectors_share_same_credentials(self):
        """Tres conectores podem ser instanciados com mesmos host/token/warehouse."""
        config = {
            "host": "https://shared.databricks.com",
            "token": "dapi_shared_token",
            "warehouse_id": "shared_wh_123",
        }
        log_src = DatabricksLogSource(**config)
        metric_src = DatabricksMetricsSource(**config)
        trace_src = DatabricksTraceSource(**config)

        for src in (log_src, metric_src, trace_src):
            assert src.host == "https://shared.databricks.com"
            assert src.token == "dapi_shared_token"
            assert src.warehouse_id == "shared_wh_123"

    @pytest.mark.unit
    def test_env_var_configuration_works_for_all(self, monkeypatch):
        """Variaveis de ambiente funcionam para os tres conectores."""
        monkeypatch.setenv("DATABRICKS_HOST", "https://env.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_env_token")
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "env_wh")

        log_src = DatabricksLogSource()
        metric_src = DatabricksMetricsSource()
        trace_src = DatabricksTraceSource()

        for src in (log_src, metric_src, trace_src):
            assert src.host == "https://env.databricks.com"
            assert src.token == "dapi_env_token"
            assert src.warehouse_id == "env_wh"

    @pytest.mark.unit
    def test_all_connectors_inherit_from_databricks_connection(self):
        """Todos os conectores herdam de DatabricksConnection."""
        assert issubclass(DatabricksLogSource, DatabricksConnection)
        assert issubclass(DatabricksMetricsSource, DatabricksConnection)
        assert issubclass(DatabricksTraceSource, DatabricksConnection)
