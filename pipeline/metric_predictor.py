from __future__ import annotations

"""
Módulo de predição de métricas: cálculo de slope EWMA e projeção ETA.

Usa extensão Rust (_aiops_rust) se disponível; caso contrário,
executa implementação Python pura.
"""

import math
import statistics
from types import ModuleType

from data.models import MetricPoint

_rust: ModuleType | None
try:
    import _aiops_rust as _rust_mod

    _rust = _rust_mod
    _RUST_AVAILABLE = hasattr(_rust_mod, "compute_ewma_slope")
except ImportError:
    _rust = None
    _RUST_AVAILABLE = False


def compute_ewma_slope(
    points: list[MetricPoint],
    span: int = 10,
    threshold: float | None = None,
    sample_interval_minutes: float | None = None,
) -> tuple[float, float | None]:
    """Calcula a inclinação EWMA e o tempo estimado até o limiar (ETA).

    Parâmetros
    ----------
    points : list[MetricPoint]
        Série temporal de pontos de métrica.
    span : int
        Janela exponencial (alpha = 2 / (span + 1)).
    threshold : float | None
        Limiar alvo para cálculo do ETA. Se None, eta_minutes será None.
    sample_interval_minutes : float | None
        Intervalo entre amostras, em minutos. Se None, é derivado da mediana
        dos deltas de timestamp de ``points`` (fallback 1.0 se não houver
        delta positivo). Deve ser > 0 e finito quando informado.

    Levanta
    -------
    ValueError
        Se ``span < 1`` ou ``sample_interval_minutes`` inválido. Pontos com
        valor não-finito (NaN/inf) são descartados antes do cálculo.

    Retorna
    -------
    (slope, eta_minutes)
        slope: inclinação POR AMOSTRA entre os dois últimos valores EWMA
        (inalterada; a conversão para minutos só ocorre no ETA).
        eta_minutes: minutos até o limiar =
        (threshold - ewma_atual) / slope * sample_interval_minutes, onde
        ewma_atual é o último valor EWMA (coerente com o slope). None se
        slope <= 0, sem limiar, ou limiar <= ewma_atual.
    """
    if sample_interval_minutes is not None and not (
        math.isfinite(sample_interval_minutes) and sample_interval_minutes > 0
    ):
        raise ValueError("sample_interval_minutes must be finite and > 0")
    if span < 1:
        raise ValueError("span must be >= 1")
    # Valores não-finitos (NaN/inf) contaminariam toda a recorrência EWMA.
    finite_points = [p for p in points if math.isfinite(p.value)]
    values = [p.value for p in finite_points]
    if len(values) < 2:
        return (0.0, None)

    if _RUST_AVAILABLE and _rust is not None:
        result = _rust.compute_ewma_slope(values, span)
        slope: float = result["slope"]
    else:
        slope = _python_ewma_slope(values, span)

    eta_minutes: float | None = None
    if slope > 0 and threshold is not None:
        current_value = _python_ewma_last(values, span)
        if threshold > current_value:
            interval = (
                sample_interval_minutes
                if sample_interval_minutes is not None
                else _derive_interval_minutes(finite_points)
            )
            eta_minutes = (threshold - current_value) / slope * interval

    return (slope, eta_minutes)


def _python_ewma_slope(values: list[float], span: int) -> float:
    """Implementação Python pura do slope EWMA.

    alpha = 2 / (span + 1); aplica suavização exponencial e retorna
    a diferença entre os dois últimos valores EWMA.
    """
    alpha = 2.0 / (span + 1)
    ewma_prev = values[0]
    ewma_last = ewma_prev

    for v in values[1:]:
        ewma_prev = ewma_last
        ewma_last = alpha * v + (1.0 - alpha) * ewma_prev

    return ewma_last - ewma_prev


def _python_ewma_last(values: list[float], span: int) -> float:
    """Último valor EWMA (mesma recorrência de ``_python_ewma_slope``)."""
    alpha = 2.0 / (span + 1)
    last = values[0]
    for v in values[1:]:
        last = alpha * v + (1.0 - alpha) * last
    return last


def _derive_interval_minutes(points: list[MetricPoint]) -> float:
    """Mediana dos deltas positivos de timestamp, em minutos (fallback 1.0)."""
    deltas = [
        (b.timestamp - a.timestamp).total_seconds() / 60.0
        for a, b in zip(points, points[1:], strict=False)
    ]
    positive = [d for d in deltas if d > 0]
    return statistics.median(positive) if positive else 1.0
