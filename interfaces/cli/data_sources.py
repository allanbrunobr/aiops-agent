"""Fontes de dados reais da CLI (``--data-source databricks``).

Factory dos conectores + etapa que converte dicts crus em entidades do
domínio. Import de ``tools.databricks_connector`` é lazy (NFR19: o modo
simulated não pode importar o módulo). Sobrevive à remoção do V0 (F5-4): o
V1 ainda não expõe ``--data-source``.
"""

from __future__ import annotations

import math
import os
import uuid
from datetime import datetime, timedelta
from typing import Any

import structlog
from rich.rule import Rule

from data.models import LogEntry, MetricPoint, SimulatedIncident, Span
from interfaces.cli.console import console

# Janela de lookback do fetch ao vivo: sem ela as métricas faziam
# None.isoformat() e voltavam vazias (PR #6).
DATABRICKS_LOOKBACK_MINUTES = 60

_persist_log = structlog.get_logger("main.persistence")


def is_valid_metric_point(point: Any) -> bool:
    """Validação de borda (B8): ponto utilizável pelo pipeline.

    Exige timestamp datetime, service/metric_name strings não vazias e value
    numérico finito (NaN/inf/None/bool/str são rejeitados). O dataclass
    ``MetricPoint`` do domínio segue sem validar — a checagem vive aqui.
    """
    value = getattr(point, "value", None)
    return (
        isinstance(getattr(point, "timestamp", None), datetime)
        and isinstance(getattr(point, "service", None), str)
        and bool(point.service)
        and isinstance(getattr(point, "metric_name", None), str)
        and bool(point.metric_name)
        and isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def drop_invalid_metric_points(incident: Any) -> int:
    """Remove in-place de ``incident.metrics`` os pontos inválidos; devolve a contagem."""
    metrics = list(getattr(incident, "metrics", None) or [])
    valid = [m for m in metrics if is_valid_metric_point(m)]
    dropped = len(metrics) - len(valid)
    if dropped:
        incident.metrics[:] = valid
        _persist_log.warning("telemetry.dropped", dropped=dropped)
    return dropped


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
    stats: dict[str, int] | None = None,
) -> tuple[SimulatedIncident, str]:
    """Etapa 1 (Databricks): Busca dados reais via conectores.

    Cada conector e chamado individualmente com try/except para fallback
    gracioso — falha em um nao impede os outros.

    Args:
        log_source: Conector de logs (LogSource).
        metrics_source: Conector de metricas (MetricsSource).
        trace_source: Conector de traces (TraceSource).
        stats: Se informado, recebe ``telemetry_dropped`` (métricas descartadas
            na borda por valor não-finito ou campos ausentes).

    Returns:
        Tupla (SimulatedIncident com dados reais, "databricks-live").
    """
    console.print(Rule("[bold]Stage 1: Databricks Data Fetch[/bold]", style="cyan"))
    console.print()

    now = datetime.now()
    # Janela explícita: sem ela as métricas faziam None.isoformat() e voltavam vazias.
    window_start = now - timedelta(minutes=DATABRICKS_LOOKBACK_MINUTES)

    # --- Logs ---
    logs: list[LogEntry] = []
    try:
        raw_logs = log_source.search(
            query="*", service="", start=window_start, end=now
        )
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
    dropped_metrics = 0
    try:
        raw_metrics = metrics_source.query(promql="", start=window_start, end=now)
        for d in raw_metrics:
            try:
                ts = (
                    datetime.fromisoformat(d["timestamp"])
                    if d.get("timestamp")
                    else now
                )
                point = MetricPoint(
                    timestamp=ts,
                    service=d.get("service", "unknown"),
                    metric_name=d.get("metric", ""),
                    # Sem default: value ausente não vira 0.0 fabricado.
                    value=float(d["value"]),
                )
            except (KeyError, TypeError, ValueError, AttributeError):
                dropped_metrics += 1
                continue
            if is_valid_metric_point(point):
                metrics.append(point)
            else:
                dropped_metrics += 1
        if dropped_metrics:
            _persist_log.warning("telemetry.dropped", dropped=dropped_metrics)
            console.print(
                f"  [yellow]Metrics:[/yellow] {dropped_metrics} malformed points dropped"
            )
        console.print(f"  [green]Metrics:[/green] {len(metrics)} points fetched")
    except Exception as exc:
        console.print(
            f"  [yellow]Metrics:[/yellow] fetch failed ({exc}), using empty list"
        )

    # --- Traces ---
    traces: list[Span] = []
    try:
        raw_traces = trace_source.find_traces(
            service="", min_duration_ms=0.0, start=window_start, end=now
        )
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

    if not (logs or metrics or traces):
        # Fallback gracioso mantido, mas nunca silencioso: o pipeline vai rodar
        # sobre nada e o diagnóstico não terá base.
        console.print(
            f"[bold red]Databricks returned no telemetry in the last "
            f"{DATABRICKS_LOOKBACK_MINUTES} minutes (logs, metrics and traces are empty). "
            f"The diagnosis below has no data behind it.[/bold red]"
        )
        _persist_log.warning(
            "databricks.no_telemetry",
            lookback_minutes=DATABRICKS_LOOKBACK_MINUTES,
        )

    if stats is not None:
        stats["telemetry_dropped"] = dropped_metrics

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


__all__ = [
    "DATABRICKS_LOOKBACK_MINUTES",
    "drop_invalid_metric_points",
    "is_valid_metric_point",
    "_create_databricks_sources",
    "step_fetch_databricks_data",
]
