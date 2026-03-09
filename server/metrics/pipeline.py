from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import structlog

from core.lifecycle import LifecycleEvent

log = structlog.get_logger()


@dataclass
class PipelineRecord:
    """Registro de execução de pipeline individual."""

    correlation_id: str
    started_at: datetime
    ended_at: datetime | None = None
    status: str = "in_progress"          # "in_progress", "completed", "failed"
    duration_ms: int | None = None
    stage_durations: dict[str, int] = field(default_factory=dict)


class PipelineMetricsCollector:
    """Coletor de métricas de execução de pipelines.

    Implementa LifecycleObserver para coletar dados automaticamente.
    Mantém registros em memória com retenção configurável.
    """

    def __init__(self, retention_hours: int = 24) -> None:
        """Inicializa coletor com período de retenção."""
        self._records: dict[str, PipelineRecord] = {}
        self._retention_hours = retention_hours
        self._last_alert_at: datetime | None = None

    async def on_event(self, event: LifecycleEvent) -> None:
        """Recebe eventos do pipeline e registra métricas."""
        if event.event_type == "pipeline.started":
            self._records[event.correlation_id] = PipelineRecord(
                correlation_id=event.correlation_id,
                started_at=event.timestamp,
            )
            self._last_alert_at = event.timestamp

        elif event.event_type == "pipeline.completed":
            record = self._records.get(event.correlation_id)
            if record:
                record.ended_at = event.timestamp
                record.status = "completed"
                record.duration_ms = event.data.get("total_duration_ms", 0)

        elif event.event_type == "pipeline.failed":
            record = self._records.get(event.correlation_id)
            if record:
                record.ended_at = event.timestamp
                record.status = "failed"
                record.duration_ms = event.data.get("total_duration_ms", 0)

        elif event.event_type == "node.completed":
            record = self._records.get(event.correlation_id)
            if record and event.node_name:
                duration = event.data.get("duration_ms", 0)
                record.stage_durations[event.node_name] = duration

    def get_pipeline_counts(self) -> dict[str, int]:
        """Retorna contagem de pipelines por status."""
        self._prune_old_data()
        counts: dict[str, int] = {"completed": 0, "failed": 0, "in_progress": 0}
        for record in self._records.values():
            counts[record.status] = counts.get(record.status, 0) + 1
        return counts

    def get_latency_distribution(self) -> dict[str, float | None]:
        """Calcula distribuição de latência (p50, p90, p99) em milissegundos."""
        self._prune_old_data()
        durations = sorted(
            r.duration_ms for r in self._records.values()
            if r.status == "completed" and r.duration_ms is not None
        )

        if not durations:
            return {"p50": None, "p90": None, "p99": None}

        return {
            "p50": _percentile(durations, 50),
            "p90": _percentile(durations, 90),
            "p99": _percentile(durations, 99),
        }

    def get_stage_durations(self) -> dict[str, dict[str, float | None]]:
        """Calcula distribuição de duração por estágio (avg, p50, p90, p99)."""
        self._prune_old_data()

        stage_data: dict[str, list[int]] = {}
        for record in self._records.values():
            if record.status != "completed":
                continue
            for stage, ms in record.stage_durations.items():
                stage_data.setdefault(stage, []).append(ms)

        result: dict[str, dict[str, float | None]] = {}
        for stage, durations in stage_data.items():
            sorted_d = sorted(durations)
            result[stage] = {
                "avg": round(sum(sorted_d) / len(sorted_d), 1),
                "p50": _percentile(sorted_d, 50),
                "p90": _percentile(sorted_d, 90),
                "p99": _percentile(sorted_d, 99),
                "count": len(sorted_d),
            }

        return result

    def get_active_count(self) -> int:
        """Retorna número de pipelines em execução."""
        return sum(1 for r in self._records.values() if r.status == "in_progress")

    def get_last_alert_at(self) -> datetime | None:
        """Retorna timestamp do último alerta recebido."""
        return self._last_alert_at

    def get_summary(self) -> dict[str, Any]:
        """Retorna resumo completo de métricas para o endpoint da API."""
        self._prune_old_data()
        return {
            "counts": self.get_pipeline_counts(),
            "latency": self.get_latency_distribution(),
            "stage_durations": self.get_stage_durations(),
            "active_count": self.get_active_count(),
            "last_alert_at": (
                self._last_alert_at.isoformat() + "Z" if self._last_alert_at else None
            ),
            "retention_hours": self._retention_hours,
        }

    def _prune_old_data(self) -> None:
        """Remove registros mais antigos que o período de retenção."""
        cutoff = datetime.utcnow() - timedelta(hours=self._retention_hours)
        to_remove = [
            cid for cid, r in self._records.items()
            if r.started_at < cutoff and r.status != "in_progress"
        ]
        for cid in to_remove:
            del self._records[cid]

        if to_remove:
            log.info(
                "metrics.pruned",
                count=len(to_remove),
                retention_hours=self._retention_hours,
            )


def _percentile(sorted_values: list[int | float], p: int) -> float:
    """Calcula percentil de lista já ordenada. Usa interpolação linear."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])

    k = (p / 100) * (n - 1)
    f = math.floor(k)
    c = math.ceil(k)

    if f == c:
        return float(sorted_values[int(k)])

    lower = sorted_values[f]
    upper = sorted_values[c]
    return round(lower + (upper - lower) * (k - f), 1)
