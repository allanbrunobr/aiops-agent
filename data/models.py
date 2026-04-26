"""Shim de re-export — entidades puras agora vivem em domain/incident.py.

Este módulo é mantido para compatibilidade com os imports existentes
(``from data.models import LogEntry, ...``). Novo código deve importar
diretamente de ``domain.incident``.
"""
from __future__ import annotations

from domain.incident import (
    LogEntry,
    MetricPoint,
    MetricSeries,
    PredictionReport,
    SimulatedIncident,
    Span,
)

__all__ = [
    "LogEntry",
    "MetricPoint",
    "MetricSeries",
    "PredictionReport",
    "SimulatedIncident",
    "Span",
]
