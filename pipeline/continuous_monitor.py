"""Vigia Barato — camada barata de monitoramento contínuo (VB2).

A cada ``tick`` consulta uma janela de métricas numa ``MetricsSource``, monta
``MetricPoint`` por métrica e reaproveita a matemática já existente (a mesma
cadeia de ``interfaces/demo/analyzer.py``): ``compute_ewma_slope`` →
``compute_risk_score`` (+ ``detect_saturation``) → ``RiskTracker.update`` →
``get_event_level``. Aplica os portões D3 de ``predictive_stage`` (série curta,
efeito relativo mínimo) para flat/ruído não alertarem.

Camada barata: NUNCA chama LLM (não importa ``adapters.llm``) e não executa
nada em infra — somente leitura de métricas.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from application.ports.telemetry_sources import MetricsSource
from data.models import MetricPoint
from pipeline.metric_predictor import compute_ewma_slope
from pipeline.predictive_stage import (
    MIN_RELATIVE_EFFECT,
    MIN_SERIES_POINTS,
    _relative_effect,
)
from pipeline.risk_scorer import (
    MetricTrendSignal,
    compute_risk_score,
    get_event_level,
    relative_slope,
)
from pipeline.risk_tracker import RiskTracker
from pipeline.saturation_detector import SaturationSignal, detect_saturation

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_MINUTES = 60
DEFAULT_ESCALATE_ETA_MINUTES = 30.0
DEFAULT_EWMA_THRESHOLD = 0.90  # mesmo default do DemoAnalyzer (métricas normalizadas)


@dataclass(frozen=True)
class MonitorTick:
    """Resultado de um tick do monitor."""

    timestamp: datetime
    score: float
    level: str | None  # "prediction.alert" | "prediction.warning" | None
    eta_minutes: float | None
    saturation: SaturationSignal | None
    should_escalate: bool


class ContinuousMonitor:
    """Loop barato: ``tick(now)`` é determinístico e testável sem sleep."""

    def __init__(
        self,
        source: MetricsSource,
        *,
        interval_seconds: float = 60,
        ewma_threshold: float | None = DEFAULT_EWMA_THRESHOLD,
        escalate_eta_minutes: float = DEFAULT_ESCALATE_ETA_MINUTES,
        tracker: RiskTracker | None = None,
        metrics: list[str] | None = None,
        ceilings: dict[str, float] | None = None,
        window_minutes: float = DEFAULT_WINDOW_MINUTES,
        step_seconds: int = 60,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds deve ser > 0")
        self.source = source
        self.interval_seconds = interval_seconds
        self.ewma_threshold = ewma_threshold
        self.escalate_eta_minutes = escalate_eta_minutes
        self.tracker = tracker if tracker is not None else RiskTracker()
        self.metrics = list(metrics) if metrics else [""]  # "" = todas (CSV)
        self.ceilings = dict(ceilings or {})
        self.window = timedelta(minutes=window_minutes)
        self.step_seconds = step_seconds
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.history: list[MonitorTick] = []

    def _points(self, now: datetime) -> dict[str, list[MetricPoint]]:
        by_name: dict[str, list[MetricPoint]] = {}
        for query in self.metrics:
            rows = self.source.query(
                query, now - self.window, now, step_seconds=self.step_seconds
            )
            for r in rows:
                ts = r["timestamp"]
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                by_name.setdefault(r["metric"], []).append(
                    MetricPoint(ts, r.get("service", ""), r["metric"], r["value"])
                )
        for pts in by_name.values():
            pts.sort(key=lambda p: p.timestamp)
        return by_name

    def tick(self, now: datetime) -> MonitorTick:
        """Avalia a janela terminada em ``now`` (sem sleep, sem relógio)."""
        best: MetricTrendSignal | None = None
        saturation: SaturationSignal | None = None
        for name, points in self._points(now).items():
            if len(points) < MIN_SERIES_POINTS:
                continue
            values = [p.value for p in points]
            sat = detect_saturation(points, self.ceilings.get(name))
            if sat is not None and (
                saturation is None
                or sat.sustained_samples > saturation.sustained_samples
            ):
                saturation = sat
            if _relative_effect(values) < MIN_RELATIVE_EFFECT:
                continue  # flat/ruído (D3)
            slope, eta = compute_ewma_slope(
                points, span=10, threshold=self.ewma_threshold
            )
            # ETA dentro do horizonte de escalada = "vai cruzar o limiar em
            # breve": sinal forte de tendência (equivale a ponto de mudança).
            imminent = (
                slope > 0 and eta is not None and eta <= self.escalate_eta_minutes
            )
            if best is None or slope > best.slope:
                best = MetricTrendSignal(
                    slope=slope,
                    eta_minutes=eta,
                    change_point_detected=imminent,
                    relative_slope=relative_slope(slope, sum(values) / len(values)),
                )
        raw = compute_risk_score(
            best, None, None, use_relative_slope=True, saturation_signal=saturation
        )
        score = self.tracker.update(raw)
        level = get_event_level(score)
        eta = best.eta_minutes if best else None
        should_escalate = level is not None and (
            level == "prediction.alert"
            or saturation is not None
            or (eta is not None and eta <= self.escalate_eta_minutes)
        )
        result = MonitorTick(now, score, level, eta, saturation, should_escalate)
        self.history.append(result)
        return result

    def run_for(self, ticks: int) -> list[MonitorTick]:
        """Executa ``ticks`` iterações com ``sleep(interval_seconds)`` entre elas."""
        out: list[MonitorTick] = []
        for i in range(ticks):
            out.append(self.tick(self._clock()))
            if i < ticks - 1:
                self._sleep(self.interval_seconds)
        return out

    def run_forever(self) -> None:  # pragma: no cover - loop infinito
        while True:
            try:
                self.tick(self._clock())
            except Exception:  # o vigia não pode morrer por uma falha de fonte
                logger.exception("continuous_monitor.tick_failed")
            self._sleep(self.interval_seconds)
