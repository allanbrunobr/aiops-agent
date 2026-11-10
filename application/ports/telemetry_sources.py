"""Portas de fontes de telemetria: métricas, logs e traces.

Movidas de tools/interfaces.py (A4): as portas ficam em application/ports;
as implementações (simuladas, Databricks, futuros MCP servers) ficam em tools/.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime


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
