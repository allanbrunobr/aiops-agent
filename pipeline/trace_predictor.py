"""
Predição de drift de latência em traces distribuídos para o pipeline AIOps.

Detecta variações de p95/p99 entre janelas temporais adjacentes nos spans
de raiz, emitindo sinais de alerta antecipado.
"""
from __future__ import annotations

from dataclasses import dataclass

from data.models import Span


@dataclass
class LatencyDriftSignal:
    """Sinal de drift de latência detectado no fluxo de spans."""

    drift_detected: bool
    p95_ratio: float
    p99_ratio: float


def _percentile(values: list[float], p: float) -> float:
    """Calcula o percentil p de uma lista de valores usando o método de índice ordenado.

    Parâmetros
    ----------
    values : list[float]
        Lista de valores numéricos.
    p : float
        Percentil desejado (0–100).

    Retorna
    -------
    float — valor no percentil p; 0.0 se a lista estiver vazia.
    """
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = (p / 100) * (len(sorted_vals) - 1)
    low = int(idx)
    high = low + 1
    frac = idx - low
    if high >= len(sorted_vals):
        return sorted_vals[low]
    return sorted_vals[low] + frac * (sorted_vals[high] - sorted_vals[low])


def detect_latency_drift(
    baseline: list[float],
    current: list[float],
    threshold_pct: float = 0.20,
) -> dict:
    """Detecta drift de latência comparando p95 e p99 entre duas janelas.

    Parâmetros
    ----------
    baseline : list[float]
        Durações em ms da janela de baseline (W1).
    current : list[float]
        Durações em ms da janela corrente (W2).
    threshold_pct : float
        Limiar fracional; drift se algum ratio >= 1 + threshold_pct.

    Retorna
    -------
    dict com chaves:
        drift_detected (bool): True se p95_ratio ou p99_ratio excede o limiar
        p95_ratio (float): p95_current / max(p95_baseline, 0.001)
        p99_ratio (float): p99_current / max(p99_baseline, 0.001)
    """
    p95_baseline = _percentile(baseline, 95)
    p95_current = _percentile(current, 95)
    p99_baseline = _percentile(baseline, 99)
    p99_current = _percentile(current, 99)

    p95_ratio = p95_current / max(p95_baseline, 0.001)
    p99_ratio = p99_current / max(p99_baseline, 0.001)

    threshold = 1.0 + threshold_pct
    drift_detected = (p95_ratio >= threshold) or (p99_ratio >= threshold)

    return {
        "drift_detected": drift_detected,
        "p95_ratio": p95_ratio,
        "p99_ratio": p99_ratio,
    }


def analyze_trace_signals(spans: list[Span]) -> LatencyDriftSignal:
    """Analisa spans de raiz e retorna sinal de drift de latência.

    Extrai spans raiz (parent_span_id is None), divide em duas metades
    e chama detect_latency_drift.

    Parâmetros
    ----------
    spans : list[Span]
        Lista completa de spans do trace a analisar.

    Retorna
    -------
    LatencyDriftSignal com os resultados.
    """
    root_spans = sorted(
        (s for s in spans if s.parent_span_id is None),
        key=lambda s: s.start_time,
    )
    root_durations = [s.duration_ms for s in root_spans]

    mid = len(root_durations) // 2
    baseline = root_durations[:mid]
    current = root_durations[mid:]

    result = detect_latency_drift(baseline, current)

    return LatencyDriftSignal(
        drift_detected=result["drift_detected"],
        p95_ratio=result["p95_ratio"],
        p99_ratio=result["p99_ratio"],
    )
