"""Fontes de dados reais da CLI (``--data-source databricks``).

Factory dos conectores + etapa que converte dicts crus em entidades do
domínio. Import de ``tools.databricks_connector`` é lazy (NFR19: o modo
simulated não pode importar o módulo). Sobrevive à remoção do V0 (F5-4): o
V1 ainda não expõe ``--data-source``.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime
from typing import Any

from rich.rule import Rule

from data.models import LogEntry, MetricPoint, SimulatedIncident, Span
from interfaces.cli.console import console


def _create_databricks_sources() -> tuple[Any, Any, Any] | None:
    """Factory que cria conectores Databricks a partir de env vars.

    Requer DATABRICKS_HOST, DATABRICKS_TOKEN e DATABRICKS_WAREHOUSE_ID.
    Retorna tupla (log_source, metrics_source, trace_source) ou None
    se alguma env var estiver ausente.
    """
    host = os.environ.get("DATABRICKS_HOST")
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")

    missing = []
    if not host:
        missing.append("DATABRICKS_HOST")
    if not token:
        missing.append("DATABRICKS_TOKEN")
    if not warehouse_id:
        missing.append("DATABRICKS_WAREHOUSE_ID")

    if missing:
        console.print(
            f"[red]Missing required env vars for Databricks: {', '.join(missing)}[/red]"
        )
        console.print("[dim]Set them in .env or export before running.[/dim]")
        return None

    # Lazy import — NFR19: simulated mode nao deve importar este modulo
    from tools.databricks_connector import (
        DatabricksLogSource,
        DatabricksMetricsSource,
        DatabricksTraceSource,
    )

    log_source = DatabricksLogSource(host=host, token=token, warehouse_id=warehouse_id)
    metrics_source = DatabricksMetricsSource(
        host=host, token=token, warehouse_id=warehouse_id
    )
    trace_source = DatabricksTraceSource(
        host=host, token=token, warehouse_id=warehouse_id
    )

    return log_source, metrics_source, trace_source


def step_fetch_databricks_data(
    log_source: Any,
    metrics_source: Any,
    trace_source: Any,
) -> tuple[SimulatedIncident, str]:
    """Etapa 1 (Databricks): Busca dados reais via conectores.

    Cada conector e chamado individualmente com try/except para fallback
    gracioso — falha em um nao impede os outros.

    Args:
        log_source: Conector de logs (LogSource).
        metrics_source: Conector de metricas (MetricsSource).
        trace_source: Conector de traces (TraceSource).

    Returns:
        Tupla (SimulatedIncident com dados reais, "databricks-live").
    """
    console.print(Rule("[bold]Stage 1: Databricks Data Fetch[/bold]", style="cyan"))
    console.print()

    now = datetime.now()

    # --- Logs ---
    logs: list[LogEntry] = []
    try:
        raw_logs = log_source.search(query="*", service="", start=None, end=None)
        for d in raw_logs:
            ts = datetime.fromisoformat(d["timestamp"]) if d.get("timestamp") else now
            logs.append(
                LogEntry(
                    timestamp=ts,
                    level=d.get("level", "INFO"),
                    service=d.get("service", "unknown"),
                    message=d.get("message", ""),
                )
            )
        console.print(f"  [green]Logs:[/green] {len(logs)} entries fetched")
    except Exception as exc:
        console.print(
            f"  [yellow]Logs:[/yellow] fetch failed ({exc}), using empty list"
        )

    # --- Metrics ---
    metrics: list[MetricPoint] = []
    try:
        raw_metrics = metrics_source.query(promql="", start=None, end=None)
        for d in raw_metrics:
            ts = datetime.fromisoformat(d["timestamp"]) if d.get("timestamp") else now
            metrics.append(
                MetricPoint(
                    timestamp=ts,
                    service=d.get("service", "unknown"),
                    metric_name=d.get("metric", ""),
                    value=float(d.get("value", 0.0)),
                )
            )
        console.print(f"  [green]Metrics:[/green] {len(metrics)} points fetched")
    except Exception as exc:
        console.print(
            f"  [yellow]Metrics:[/yellow] fetch failed ({exc}), using empty list"
        )

    # --- Traces ---
    traces: list[Span] = []
    try:
        raw_traces = trace_source.find_traces(service="", min_duration_ms=0.0)
        for d in raw_traces:
            traces.append(
                Span(
                    trace_id=d.get("trace_id", ""),
                    span_id=str(uuid.uuid4()),
                    parent_span_id=None,
                    service=d.get("service", "unknown"),
                    operation=d.get("operation", ""),
                    start_time=now,
                    duration_ms=float(d.get("duration_ms", 0.0)),
                    status=d.get("status", "unknown"),
                    attributes=d.get("attributes", {}),
                )
            )
        console.print(f"  [green]Traces:[/green] {len(traces)} spans fetched")
    except Exception as exc:
        console.print(
            f"  [yellow]Traces:[/yellow] fetch failed ({exc}), using empty list"
        )

    incident = SimulatedIncident(
        logs=logs,
        metrics=metrics,
        traces=traces,
        deploy_time=now,
        spike_start=now,
        detection_time=now,
        fix_time=now,
        actual_root_cause="unknown (live data)",
        actual_fix="unknown (live data)",
    )
    console.print()
    return incident, "databricks-live"


__all__ = ["_create_databricks_sources", "step_fetch_databricks_data"]
