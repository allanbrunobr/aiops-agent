"""
Contratos de dados (dataclasses) compartilhados pelo pipeline AIOps.

Estas dataclasses são os contratos "hub" usados por todo o sistema:
LogEntry, MetricPoint, Span e SimulatedIncident.

ATENÇÃO: Adicionar ou remover campos é uma breaking change — todos os
consumidores dependem da forma exata destes contratos.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field


@dataclass
class LogEntry:
    """Entrada de log com timestamp, nível, serviço e mensagem."""
    timestamp: datetime.datetime
    level: str
    service: str
    message: str

    def __str__(self):
        ts = self.timestamp.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        return f"{ts} {self.level} [{self.service}] {self.message}"


@dataclass
class MetricPoint:
    """Ponto individual de uma série temporal de métrica."""
    timestamp: datetime.datetime
    service: str
    metric_name: str
    value: float


@dataclass
class Span:
    """Span de um trace distribuído."""
    trace_id: str
    span_id: str
    parent_span_id: str | None
    service: str
    operation: str
    start_time: datetime.datetime
    duration_ms: float
    status: str
    attributes: dict = field(default_factory=dict)


@dataclass
class SimulatedIncident:
    """Container com todos os dados do incidente simulado."""
    logs: list[LogEntry]
    metrics: list[MetricPoint]
    traces: list[Span]
    deploy_time: datetime.datetime = datetime.datetime(2026, 3, 4, 14, 19, 0)
    spike_start: datetime.datetime = datetime.datetime(2026, 3, 4, 14, 23, 0)
    detection_time: datetime.datetime = datetime.datetime(2026, 3, 4, 14, 25, 0)
    fix_time: datetime.datetime = datetime.datetime(2026, 3, 4, 14, 28, 0)
    actual_root_cause: str = (
        "Config change no deploy v2.3.1 reduziu max_connections de 200 para 50, "
        "causando connection pool exhaustion sob carga normal."
    )
    actual_fix: str = "Rollback config max_connections para 200"


@dataclass
class MetricSeries:
    """Série temporal de uma métrica com suporte a projeção de limiar."""
    service: str
    metric_name: str
    points: list[MetricPoint]
    threshold: float | None = None
    eta_minutes: float | None = None
