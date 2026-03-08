"""Databricks System Tables connector for observability data.

Provides DatabricksConnection base class (auth, timeout, error handling)
and three connectors implementing their respective ABCs:
- DatabricksLogSource (LogSource) — audit logs
- DatabricksMetricsSource (MetricsSource) — billing/usage metrics
- DatabricksTraceSource (TraceSource) — query history traces
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from tools.interfaces import LogSource, MetricsSource, TraceSource


# Mapeamento padrao de colunas do system.access.audit para LogEntry
DEFAULT_COLUMN_MAPPING = {
    "timestamp": "event_time",
    "level": "action_name",
    "service": "service_name",
    "message": "request_params",
}

# Mapeamento padrao de colunas do system.billing.usage para MetricPoint
DEFAULT_METRICS_COLUMN_MAPPING = {
    "timestamp": "usage_date",
    "service": "sku_name",
    "metric_name": "usage_unit",
    "value": "usage_quantity",
}

# Mapeamento padrao de alertas (sem tabela padrao no Databricks)
DEFAULT_ALERTS_COLUMN_MAPPING = {
    "alert": "alert_name",
    "service": "service_name",
    "severity": "severity",
    "status": "status",
}

# Mapeamento padrao de colunas do system.query.history para Span
DEFAULT_TRACES_COLUMN_MAPPING = {
    "trace_id": "query_id",
    "service": "warehouse_id",
    "operation": "statement_type",
    "start_time": "query_start_time_ms",
    "duration_ms": "duration",
    "status": "status",
}


@dataclass
class DatabricksConnectorError(Exception):
    """Erro estruturado para falhas de conexao Databricks."""

    service: str
    error_type: str
    timestamp: str
    details: str

    def __post_init__(self) -> None:
        """Inicializa Exception.args para compatibilidade com traceback."""
        super().__init__(self.details)

    def __str__(self) -> str:
        """Representacao textual do erro."""
        return (
            f"[{self.error_type}] {self.service} at {self.timestamp}: "
            f"{self.details}"
        )


class DatabricksConnection:
    """Base para conectores Databricks com auth, timeout e error handling.

    Subclasses herdam autenticacao, execucao de queries e tratamento
    de erros padronizado. Lazy-import do databricks-sdk evita custo
    de startup quando o conector nao e utilizado.
    """

    def __init__(
        self,
        host: str | None = None,
        token: str | None = None,
        warehouse_id: str | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        """Inicializa conexao com Databricks.

        Parametros podem ser passados explicitamente ou lidos de
        variaveis de ambiente: DATABRICKS_HOST, DATABRICKS_TOKEN,
        DATABRICKS_WAREHOUSE_ID.
        """
        self.host = host or os.environ.get("DATABRICKS_HOST")
        self.token = token or os.environ.get("DATABRICKS_TOKEN")
        self.warehouse_id = warehouse_id or os.environ.get(
            "DATABRICKS_WAREHOUSE_ID"
        )
        self.timeout_seconds = timeout_seconds
        self._client = None
        self._validate_config()

    def _validate_config(self) -> None:
        """Valida que todas as configuracoes obrigatorias estao presentes."""
        missing = []
        if not self.host:
            missing.append("host (or DATABRICKS_HOST env var)")
        if not self.token:
            missing.append("token (or DATABRICKS_TOKEN env var)")
        if not self.warehouse_id:
            missing.append("warehouse_id (or DATABRICKS_WAREHOUSE_ID env var)")

        if missing:
            raise DatabricksConnectorError(
                service=self.__class__.__name__,
                error_type="config",
                timestamp=datetime.now().isoformat(),
                details=(
                    "Missing required configuration: "
                    + ", ".join(missing)
                ),
            )

    def _get_client(self):
        """Retorna cliente Databricks com lazy-import do SDK."""
        if self._client is None:
            from databricks.sdk import WorkspaceClient

            self._client = WorkspaceClient(
                host=self.host,
                token=self.token,
            )
        return self._client

    def _execute_query(self, sql: str) -> list[dict]:
        """Executa query SQL via Databricks Statement Execution API.

        Retorna lista de dicts com os resultados. Todas as excecoes
        sao capturadas e convertidas em DatabricksConnectorError.
        """
        try:
            client = self._get_client()
            response = client.statement_execution.execute_statement(
                warehouse_id=self.warehouse_id,
                statement=sql,
                wait_timeout=f"{self.timeout_seconds}s",
            )

            if response.status and response.status.state:
                state_value = str(response.status.state.value)
                if state_value != "SUCCEEDED":
                    error_msg = ""
                    if response.status.error:
                        error_msg = str(response.status.error.message)
                    raise DatabricksConnectorError(
                        service=self.__class__.__name__,
                        error_type="query",
                        timestamp=datetime.now().isoformat(),
                        details=(
                            f"Query failed with state {state_value}: "
                            f"{error_msg}"
                        ),
                    )

            # Extrai resultados do response
            if not response.manifest or not response.result:
                return []

            columns = [
                col.name for col in response.manifest.schema.columns
            ]
            rows = []
            if response.result.data_array:
                for row_data in response.result.data_array:
                    rows.append(dict(zip(columns, row_data)))

            return rows

        except DatabricksConnectorError:
            raise
        except Exception as e:
            raise DatabricksConnectorError(
                service=self.__class__.__name__,
                error_type=self._classify_error(e),
                timestamp=datetime.now().isoformat(),
                details=f"Query failed: {e}",
            ) from e

    @staticmethod
    def _classify_error(error: Exception) -> str:
        """Classifica excecao em tipo de erro estruturado."""
        error_type_name = type(error).__name__.lower()
        error_msg = str(error).lower()

        # Timeout / conexao
        if any(
            kw in error_type_name
            for kw in ("timeout", "connection", "network")
        ):
            return "timeout"
        if any(
            kw in error_msg
            for kw in ("timeout", "timed out", "connection refused")
        ):
            return "timeout"

        # Autenticacao
        if any(
            kw in error_type_name
            for kw in ("permission", "auth", "forbidden", "unauthorized")
        ):
            return "auth"
        if any(
            kw in error_msg
            for kw in (
                "401", "403", "unauthorized", "forbidden",
                "authentication", "permission denied",
            )
        ):
            return "auth"

        # Query / outros
        return "query"


class DatabricksLogSource(DatabricksConnection, LogSource):
    """Fonte de logs real via Databricks System Tables.

    Implementa a interface LogSource (tools/interfaces.py) consultando
    tabelas de auditoria do Databricks. O mapeamento de colunas e
    configuravel para suportar diferentes tabelas-fonte.
    """

    def __init__(
        self,
        host: str | None = None,
        token: str | None = None,
        warehouse_id: str | None = None,
        timeout_seconds: int = 30,
        table: str = "system.access.audit",
        column_mapping: dict | None = None,
    ) -> None:
        """Inicializa fonte de logs Databricks.

        Alem dos parametros de conexao herdados de DatabricksConnection,
        aceita o nome da tabela-alvo e mapeamento de colunas customizado.
        """
        super().__init__(host, token, warehouse_id, timeout_seconds)
        self.table = table
        self.column_mapping = column_mapping or DEFAULT_COLUMN_MAPPING.copy()

    # --- LogSource ABC implementation ---

    def search(
        self,
        query: str,
        service: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Busca logs por query na tabela Databricks.

        Constroi SQL dinâmico contra a tabela configurada, aplicando
        filtros de servico, janela temporal e texto. Retorna list[dict]
        compativel com o contrato LogSource.search().
        """
        try:
            ts_col = self.column_mapping["timestamp"]
            svc_col = self.column_mapping["service"]
            msg_col = self.column_mapping["message"]

            where_clauses = []
            if query:
                safe_query = query.replace("'", "''")
                where_clauses.append(
                    f"CAST({msg_col} AS STRING) LIKE '%{safe_query}%'"
                )
            if service:
                safe_svc = service.replace("'", "''")
                where_clauses.append(f"{svc_col} = '{safe_svc}'")
            if start:
                where_clauses.append(
                    f"{ts_col} >= '{start.isoformat()}'"
                )
            if end:
                where_clauses.append(
                    f"{ts_col} <= '{end.isoformat()}'"
                )

            where_sql = (
                "WHERE " + " AND ".join(where_clauses)
                if where_clauses
                else ""
            )

            sql = (
                f"SELECT * FROM {self.table} "
                f"{where_sql} "
                f"ORDER BY {ts_col} DESC "
                f"LIMIT {limit}"
            )

            rows = self._execute_query(sql)
        except DatabricksConnectorError:
            raise
        except Exception as e:
            raise DatabricksConnectorError(
                service=self.__class__.__name__,
                error_type=self._classify_error(e),
                timestamp=datetime.now().isoformat(),
                details=f"Search query failed: {e}",
            ) from e

        return self._map_rows(rows, include_service=True)

    def tail(self, service: str, lines: int = 50) -> list[dict]:
        """Retorna ultimas N linhas de log de um servico.

        Consulta tabela ordenada por timestamp descendente, limitada
        a `lines` registros. Retorna list[dict] compativel com
        o contrato LogSource.tail().
        """
        try:
            ts_col = self.column_mapping["timestamp"]
            svc_col = self.column_mapping["service"]

            safe_svc = service.replace("'", "''")

            sql = (
                f"SELECT * FROM {self.table} "
                f"WHERE {svc_col} = '{safe_svc}' "
                f"ORDER BY {ts_col} DESC "
                f"LIMIT {lines}"
            )

            rows = self._execute_query(sql)
        except DatabricksConnectorError:
            raise
        except Exception as e:
            raise DatabricksConnectorError(
                service=self.__class__.__name__,
                error_type=self._classify_error(e),
                timestamp=datetime.now().isoformat(),
                details=f"Tail query failed: {e}",
            ) from e

        return self._map_rows(rows, include_service=False)

    def _map_rows(
        self, rows: list[dict], *, include_service: bool
    ) -> list[dict]:
        """Converte linhas do Databricks para o formato LogEntry dict.

        Aplica o column_mapping configurado para mapear colunas
        da tabela-fonte para os campos padrao (timestamp, level,
        service, message).
        """
        results = []
        ts_col = self.column_mapping["timestamp"]
        lvl_col = self.column_mapping["level"]
        svc_col = self.column_mapping["service"]
        msg_col = self.column_mapping["message"]

        for row in rows:
            # Timestamp: converter para ISO string se necessario
            ts_value = row.get(ts_col, "")
            if isinstance(ts_value, datetime):
                ts_value = ts_value.isoformat()
            else:
                ts_value = str(ts_value) if ts_value else ""

            entry = {
                "timestamp": ts_value,
                "level": str(row.get(lvl_col, "INFO")),
                "message": str(row.get(msg_col, "")),
            }

            if include_service:
                entry["service"] = str(row.get(svc_col, "unknown"))

            results.append(entry)

        return results


class DatabricksMetricsSource(DatabricksConnection, MetricsSource):
    """Fonte de metricas real via Databricks System Tables.

    Implementa a interface MetricsSource (tools/interfaces.py) consultando
    tabelas de uso/billing do Databricks. O mapeamento de colunas e
    configuravel para suportar diferentes tabelas-fonte.
    """

    def __init__(
        self,
        host: str | None = None,
        token: str | None = None,
        warehouse_id: str | None = None,
        timeout_seconds: int = 30,
        table: str = "system.billing.usage",
        column_mapping: dict | None = None,
        alerts_table: str | None = None,
        alerts_column_mapping: dict | None = None,
    ) -> None:
        """Inicializa fonte de metricas Databricks.

        Alem dos parametros de conexao herdados de DatabricksConnection,
        aceita o nome da tabela-alvo, mapeamento de colunas e configuracao
        opcional de tabela de alertas.
        """
        super().__init__(host, token, warehouse_id, timeout_seconds)
        self.table = table
        self.column_mapping = (
            column_mapping or DEFAULT_METRICS_COLUMN_MAPPING.copy()
        )
        self.alerts_table = alerts_table
        self.alerts_column_mapping = (
            alerts_column_mapping or DEFAULT_ALERTS_COLUMN_MAPPING.copy()
        )

    # --- MetricsSource ABC implementation ---

    def query(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 15,
    ) -> list[dict]:
        """Busca metricas por filtro de texto na tabela Databricks.

        O parametro promql e usado como filtro LIKE contra as colunas
        de metrica e servico (Databricks nao tem motor PromQL nativo).
        step_seconds e aceito por compatibilidade com a ABC mas nao
        aplica downsampling no SQL.
        Retorna list[dict] compativel com o contrato MetricsSource.query().
        """
        try:
            ts_col = self.column_mapping["timestamp"]
            svc_col = self.column_mapping["service"]
            metric_col = self.column_mapping["metric_name"]
            val_col = self.column_mapping["value"]

            safe_promql = promql.replace("'", "''")

            where_clauses = [
                f"{ts_col} >= '{start.isoformat()}'",
                f"{ts_col} <= '{end.isoformat()}'",
                (
                    f"(CAST({metric_col} AS STRING) LIKE '%{safe_promql}%'"
                    f" OR CAST({svc_col} AS STRING) LIKE '%{safe_promql}%')"
                ),
            ]

            where_sql = "WHERE " + " AND ".join(where_clauses)

            sql = (
                f"SELECT * FROM {self.table} "
                f"{where_sql} "
                f"ORDER BY {ts_col} DESC "
                f"LIMIT 100"
            )

            rows = self._execute_query(sql)
        except DatabricksConnectorError:
            raise
        except Exception as e:
            raise DatabricksConnectorError(
                service=self.__class__.__name__,
                error_type=self._classify_error(e),
                timestamp=datetime.now().isoformat(),
                details=f"Metrics query failed: {e}",
            ) from e

        return self._map_metric_rows(rows)

    def get_alerts(self, active_only: bool = True) -> list[dict]:
        """Retorna alertas ativos da tabela de alertas configurada.

        Quando nenhuma tabela de alertas esta configurada (alerts_table=None),
        retorna lista vazia como degradacao graciosa, ja que Databricks
        System Tables nao possuem tabela nativa de alertas.
        """
        if self.alerts_table is None:
            return []

        try:
            alert_col = self.alerts_column_mapping["alert"]
            svc_col = self.alerts_column_mapping["service"]
            severity_col = self.alerts_column_mapping["severity"]

            status_col = self.alerts_column_mapping.get("status", "status")

            where_sql = ""
            if active_only:
                where_sql = f"WHERE {status_col} = 'active'"

            sql = (
                f"SELECT {alert_col}, {svc_col}, {severity_col} "
                f"FROM {self.alerts_table} "
                f"{where_sql} "
                f"ORDER BY {severity_col}"
            )

            rows = self._execute_query(sql)
        except DatabricksConnectorError:
            raise
        except Exception as e:
            raise DatabricksConnectorError(
                service=self.__class__.__name__,
                error_type=self._classify_error(e),
                timestamp=datetime.now().isoformat(),
                details=f"Alerts query failed: {e}",
            ) from e

        return self._map_alert_rows(rows)

    def _map_metric_rows(self, rows: list[dict]) -> list[dict]:
        """Converte linhas do Databricks para o formato MetricPoint dict.

        Aplica o column_mapping configurado para mapear colunas
        da tabela-fonte para os campos padrao (timestamp, service,
        metric, value).
        """
        results = []
        ts_col = self.column_mapping["timestamp"]
        svc_col = self.column_mapping["service"]
        metric_col = self.column_mapping["metric_name"]
        val_col = self.column_mapping["value"]

        for row in rows:
            ts_value = row.get(ts_col, "")
            if isinstance(ts_value, datetime):
                ts_value = ts_value.isoformat()
            else:
                ts_value = str(ts_value) if ts_value else ""

            val_raw = row.get(val_col, 0)
            try:
                value = float(val_raw) if val_raw is not None else 0.0
            except (ValueError, TypeError):
                value = 0.0

            results.append({
                "timestamp": ts_value,
                "service": str(row.get(svc_col, "unknown")),
                "metric": str(row.get(metric_col, "")),
                "value": value,
            })

        return results

    def _map_alert_rows(self, rows: list[dict]) -> list[dict]:
        """Converte linhas da tabela de alertas para o formato padrao.

        Aplica o alerts_column_mapping configurado para mapear colunas
        para os campos padrao (alert, service, severity).
        """
        results = []
        alert_col = self.alerts_column_mapping["alert"]
        svc_col = self.alerts_column_mapping["service"]
        severity_col = self.alerts_column_mapping["severity"]

        for row in rows:
            results.append({
                "alert": str(row.get(alert_col, "")),
                "service": str(row.get(svc_col, "unknown")),
                "severity": str(row.get(severity_col, "unknown")),
            })

        return results


class DatabricksTraceSource(DatabricksConnection, TraceSource):
    """Fonte de traces real via Databricks System Tables.

    Implementa a interface TraceSource (tools/interfaces.py) consultando
    o historico de queries do Databricks. O mapeamento de colunas e
    configuravel para suportar diferentes tabelas-fonte.
    """

    def __init__(
        self,
        host: str | None = None,
        token: str | None = None,
        warehouse_id: str | None = None,
        timeout_seconds: int = 30,
        table: str = "system.query.history",
        column_mapping: dict | None = None,
    ) -> None:
        """Inicializa fonte de traces Databricks.

        Alem dos parametros de conexao herdados de DatabricksConnection,
        aceita o nome da tabela-alvo e mapeamento de colunas customizado.
        """
        super().__init__(host, token, warehouse_id, timeout_seconds)
        self.table = table
        self.column_mapping = (
            column_mapping or DEFAULT_TRACES_COLUMN_MAPPING.copy()
        )

    # --- TraceSource ABC implementation ---

    def find_traces(
        self,
        service: str,
        min_duration_ms: float | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Busca traces por servico e duracao na tabela Databricks.

        Constroi SQL dinamico contra a tabela configurada, aplicando
        filtros de servico, duracao minima e janela temporal.
        Retorna list[dict] compativel com o contrato TraceSource.find_traces().
        """
        try:
            svc_col = self.column_mapping["service"]
            duration_col = self.column_mapping["duration_ms"]
            start_col = self.column_mapping["start_time"]

            safe_svc = service.replace("'", "''")

            where_clauses = [f"{svc_col} = '{safe_svc}'"]

            if min_duration_ms is not None:
                where_clauses.append(
                    f"{duration_col} >= {min_duration_ms}"
                )
            if start:
                where_clauses.append(
                    f"{start_col} >= '{start.isoformat()}'"
                )
            if end:
                where_clauses.append(
                    f"{start_col} <= '{end.isoformat()}'"
                )

            where_sql = "WHERE " + " AND ".join(where_clauses)

            sql = (
                f"SELECT * FROM {self.table} "
                f"{where_sql} "
                f"ORDER BY {start_col} DESC "
                f"LIMIT {limit}"
            )

            rows = self._execute_query(sql)
        except DatabricksConnectorError:
            raise
        except Exception as e:
            raise DatabricksConnectorError(
                service=self.__class__.__name__,
                error_type=self._classify_error(e),
                timestamp=datetime.now().isoformat(),
                details=f"Find traces query failed: {e}",
            ) from e

        return self._map_trace_rows(rows)

    def get_trace(self, trace_id: str) -> dict:
        """Retorna trace completo com todos os spans.

        Databricks system.query.history armazena uma linha por query,
        sem hierarquia parent/child de spans. Retorna o resultado
        como dict com lista de um unico span, compativel com o
        contrato TraceSource.get_trace().
        """
        try:
            tid_col = self.column_mapping["trace_id"]

            safe_tid = trace_id.replace("'", "''")

            sql = (
                f"SELECT * FROM {self.table} "
                f"WHERE {tid_col} = '{safe_tid}' "
                f"LIMIT 1"
            )

            rows = self._execute_query(sql)
        except DatabricksConnectorError:
            raise
        except Exception as e:
            raise DatabricksConnectorError(
                service=self.__class__.__name__,
                error_type=self._classify_error(e),
                timestamp=datetime.now().isoformat(),
                details=f"Get trace query failed: {e}",
            ) from e

        if not rows:
            return {"trace_id": trace_id, "spans": []}

        spans = self._map_trace_rows(rows)
        return {
            "trace_id": trace_id,
            "spans": [
                {
                    "service": s["service"],
                    "operation": s["operation"],
                    "duration_ms": s["duration_ms"],
                    "status": s["status"],
                }
                for s in spans
            ],
        }

    def _map_trace_rows(self, rows: list[dict]) -> list[dict]:
        """Converte linhas do Databricks para o formato Span dict.

        Aplica o column_mapping configurado para mapear colunas
        da tabela-fonte para os campos padrao (trace_id, service,
        operation, duration_ms, status, attributes).
        """
        results = []
        tid_col = self.column_mapping["trace_id"]
        svc_col = self.column_mapping["service"]
        op_col = self.column_mapping["operation"]
        dur_col = self.column_mapping["duration_ms"]
        status_col = self.column_mapping["status"]

        for row in rows:
            dur_raw = row.get(dur_col, 0)
            try:
                duration = float(dur_raw) if dur_raw is not None else 0.0
            except (ValueError, TypeError):
                duration = 0.0

            results.append({
                "trace_id": str(row.get(tid_col, "")),
                "service": str(row.get(svc_col, "unknown")),
                "operation": str(row.get(op_col, "")),
                "duration_ms": duration,
                "status": str(row.get(status_col, "unknown")),
                "attributes": {},
            })

        return results
