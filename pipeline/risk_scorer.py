from __future__ import annotations

"""
Cálculo de score de risco unificado combinando sinais preditivos.

Pondera MetricTrendSignal, LogBurstSignal e LatencyDriftSignal em
um score único de 0 a 100 e determina o nível de evento de lifecycle.
"""

from dataclasses import dataclass

from pipeline.log_predictor import LogBurstSignal
from pipeline.trace_predictor import LatencyDriftSignal


@dataclass
class MetricTrendSignal:
    """Sinal de tendência de métrica a partir da inclinação EWMA."""

    slope: float
    eta_minutes: float | None = None
    change_point_detected: bool = False
    # slope / max(|média da série|, epsilon): independente de unidade (D3).
    relative_slope: float | None = None


# Limiares ABSOLUTOS (default, dependem da unidade da métrica) — D-e6.
_ABS_SLOPE_HIGH = 0.5
_ABS_SLOPE_LOW = 0.1
# Limiares RELATIVOS (opt-in): fração da média da série por amostra.
# 5%/amostra ≈ tendência forte; 1%/amostra ≈ tendência moderada.
_REL_SLOPE_HIGH = 0.05
_REL_SLOPE_LOW = 0.01
# Piso do denominador do slope relativo (evita divisão por ~0).
RELATIVE_SLOPE_EPSILON = 1e-9


def relative_slope(slope: float, mean_value: float) -> float:
    """Slope relativo = slope / max(|média|, epsilon)."""
    return slope / max(abs(mean_value), RELATIVE_SLOPE_EPSILON)


def _score_metric_signal(
    signal: MetricTrendSignal | None, use_relative_slope: bool = False
) -> float:
    """Converte MetricTrendSignal em pontuação de 0.0 a 100.0.

    Com ``use_relative_slope=True`` e ``signal.relative_slope`` disponível,
    os limiares são relativos (``_REL_SLOPE_*``); caso contrário, o
    comportamento absoluto original (``_ABS_SLOPE_*``) é mantido.
    """
    if signal is None:
        return 0.0
    if signal.change_point_detected:
        return 80.0
    if use_relative_slope and signal.relative_slope is not None:
        value, high, low = signal.relative_slope, _REL_SLOPE_HIGH, _REL_SLOPE_LOW
    else:
        value, high, low = signal.slope, _ABS_SLOPE_HIGH, _ABS_SLOPE_LOW
    if value > high:
        return 60.0
    if value > low:
        return 30.0
    return 0.0


def _score_log_signal(signal: LogBurstSignal | None) -> float:
    """Converte LogBurstSignal em pontuação de 0.0 a 100.0."""
    if signal is None:
        return 0.0
    if not signal.burst_detected:
        base = 0.0
    else:
        # Clamp inferior: ratio<1 (ou lixo negativo) não pode gerar score
        # negativo e mascarar a contribuição de novel_templates / outros sinais.
        base = max(0.0, min(100.0, (signal.rate_ratio - 1.0) * 50.0))
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
    use_relative_slope: bool = False,
) -> float:
    """Combina sinais preditivos em score de risco de 0 a 100 (FR-P6).

    Pesos: metric_slope=40%, log_burst=30%, latency_drift=30%.
    ``use_relative_slope`` troca os limiares absolutos de slope pelos relativos.
    Retorna 0.0 se todos os sinais forem None.
    """
    metric_score = _score_metric_signal(metric_signal, use_relative_slope)
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
