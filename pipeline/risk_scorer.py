from __future__ import annotations
"""
Cálculo de score de risco unificado combinando sinais preditivos.

Pondera MetricTrendSignal, LogBurstSignal e LatencyDriftSignal em
um score único de 0 a 100 e determina o nível de evento de lifecycle.
"""

from dataclasses import dataclass, field

from pipeline.log_predictor import LogBurstSignal
from pipeline.trace_predictor import LatencyDriftSignal


@dataclass
class MetricTrendSignal:
    """Sinal de tendência de métrica a partir da inclinação EWMA."""
    slope: float
    eta_minutes: float | None = None
    change_point_detected: bool = False


def _score_metric_signal(signal: MetricTrendSignal | None) -> float:
    """Converte MetricTrendSignal em pontuação de 0.0 a 100.0."""
    if signal is None:
        return 0.0
    if signal.change_point_detected:
        return 80.0
    if signal.slope > 0.5:
        return 60.0
    if signal.slope > 0.1:
        return 30.0
    return 10.0


def _score_log_signal(signal: LogBurstSignal | None) -> float:
    """Converte LogBurstSignal em pontuação de 0.0 a 100.0."""
    if signal is None:
        return 0.0
    if not signal.burst_detected:
        base = 0.0
    else:
        base = min(100.0, (signal.rate_ratio - 1.0) * 50.0)
    if signal.novel_templates:
        base += 20.0
    return min(100.0, base)


def _score_latency_signal(signal: LatencyDriftSignal | None) -> float:
    """Converte LatencyDriftSignal em pontuação de 0.0 a 100.0."""
    if signal is None:
        return 0.0
    if not signal.drift_detected:
        return 0.0
    ratio = max(signal.p95_ratio, signal.p99_ratio)
    return min(100.0, (ratio - 1.0) * 200.0)


def compute_risk_score(
    metric_signal: MetricTrendSignal | None,
    log_signal: LogBurstSignal | None,
    latency_signal: LatencyDriftSignal | None,
) -> float:
    """Combina sinais preditivos em score de risco de 0 a 100 (FR-P6).

    Pesos: metric_slope=40%, log_burst=30%, latency_drift=30%.
    Retorna 0.0 se todos os sinais forem None.
    """
    metric_score = _score_metric_signal(metric_signal)
    log_score = _score_log_signal(log_signal)
    latency_score = _score_latency_signal(latency_signal)

    weighted = 0.4 * metric_score + 0.3 * log_score + 0.3 * latency_score
    return min(100.0, max(0.0, weighted))


def get_event_level(risk_score: float) -> str | None:
    """Determina o nível de evento de lifecycle baseado no score de risco.

    Retorna 'prediction.alert' se score >= 60,
    'prediction.warning' se 30 <= score < 60, ou None.
    """
    if risk_score >= 60:
        return "prediction.alert"
    if risk_score >= 30:
        return "prediction.warning"
    return None
