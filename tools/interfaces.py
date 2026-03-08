"""
Interfaces abstratas para fontes de dados de observabilidade.

Prontas para plugar MCP servers no futuro:
  - prometheus-mcp-server (métricas)
  - grafana-mcp-server (dashboards)
  - datadog-mcp-server (logs/APM)
  - jira-mcp-server (issue tracking)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from dataclasses import dataclass


# --- Interfaces Abstratas (para MCP futuro) ---

class MetricsSource(ABC):
    """Interface para fonte de métricas (ex: Prometheus via MCP)."""

    @abstractmethod
    def query(self, promql: str, start: datetime, end: datetime,
              step_seconds: int = 15) -> list[dict]:
        """Executa query PromQL e retorna time series."""
        ...

    @abstractmethod
    def get_alerts(self, active_only: bool = True) -> list[dict]:
        """Retorna alertas ativos."""
        ...


class LogSource(ABC):
    """Interface para fonte de logs (ex: Datadog/ELK via MCP)."""

    @abstractmethod
    def search(self, query: str, service: str | None = None,
               start: datetime | None = None, end: datetime | None = None,
               limit: int = 100) -> list[dict]:
        """Busca logs por query."""
        ...

    @abstractmethod
    def tail(self, service: str, lines: int = 50) -> list[dict]:
        """Retorna últimas N linhas de um serviço."""
        ...


class TraceSource(ABC):
    """Interface para fonte de traces (ex: Jaeger/Grafana Tempo via MCP)."""

    @abstractmethod
    def find_traces(self, service: str, min_duration_ms: float | None = None,
                    start: datetime | None = None, end: datetime | None = None,
                    limit: int = 20) -> list[dict]:
        """Busca traces por serviço e duração."""
        ...

    @abstractmethod
    def get_trace(self, trace_id: str) -> dict:
        """Retorna trace completo com todos os spans."""
        ...


class IncidentTracker(ABC):
    """Interface para tracking de incidentes (ex: Jira/PagerDuty via MCP)."""

    @abstractmethod
    def create_incident(self, title: str, severity: str, description: str) -> str:
        """Cria incidente e retorna ID."""
        ...

    @abstractmethod
    def create_postmortem(self, incident_id: str, timeline: list[dict],
                          root_cause: str, action_items: list[str]) -> str:
        """Cria documento de postmortem."""
        ...


# --- Implementações Simuladas (para o protótipo) ---

class SimulatedMetricsSource(MetricsSource):
    """Fonte de métricas simulada usando dados do sample_incident."""

    def __init__(self, metrics: list):
        self.metrics = metrics

    def query(self, promql: str, start: datetime, end: datetime,
              step_seconds: int = 15) -> list[dict]:
        """Simula query PromQL filtrando dados em memória."""
        # Parse simples do PromQL para extrair metric_name e service
        results = []
        for m in self.metrics:
            if start <= m.timestamp <= end:
                if promql.lower() in m.metric_name.lower() or promql.lower() in m.service.lower():
                    results.append({
                        "timestamp": m.timestamp.isoformat(),
                        "service": m.service,
                        "metric": m.metric_name,
                        "value": m.value,
                    })
        return results[:100]

    def get_alerts(self, active_only: bool = True) -> list[dict]:
        return [{"alert": "HighErrorRate", "service": "payment-svc", "severity": "critical"}]


class SimulatedLogSource(LogSource):
    """Fonte de logs simulada."""

    def __init__(self, logs: list):
        self.logs = logs

    def search(self, query: str, service: str | None = None,
               start: datetime | None = None, end: datetime | None = None,
               limit: int = 100) -> list[dict]:
        results = []
        for log in self.logs:
            if service and log.service != service:
                continue
            if start and log.timestamp < start:
                continue
            if end and log.timestamp > end:
                continue
            if query.lower() in log.message.lower():
                results.append({
                    "timestamp": log.timestamp.isoformat(),
                    "level": log.level,
                    "service": log.service,
                    "message": log.message,
                })
            if len(results) >= limit:
                break
        return results

    def tail(self, service: str, lines: int = 50) -> list[dict]:
        svc_logs = [l for l in self.logs if l.service == service]
        return [{"timestamp": l.timestamp.isoformat(), "level": l.level,
                 "message": l.message} for l in svc_logs[-lines:]]


class SimulatedTraceSource(TraceSource):
    """Fonte de traces simulada."""

    def __init__(self, traces: list):
        self.traces = traces

    def find_traces(self, service: str, min_duration_ms: float | None = None,
                    start: datetime | None = None, end: datetime | None = None,
                    limit: int = 20) -> list[dict]:
        results = []
        for span in self.traces:
            if span.service != service:
                continue
            if min_duration_ms and span.duration_ms < min_duration_ms:
                continue
            results.append({
                "trace_id": span.trace_id,
                "service": span.service,
                "operation": span.operation,
                "duration_ms": span.duration_ms,
                "status": span.status,
                "attributes": span.attributes,
            })
            if len(results) >= limit:
                break
        return results

    def get_trace(self, trace_id: str) -> dict:
        spans = [s for s in self.traces if s.trace_id == trace_id]
        return {"trace_id": trace_id, "spans": [
            {"service": s.service, "operation": s.operation,
             "duration_ms": s.duration_ms, "status": s.status}
            for s in spans
        ]}
