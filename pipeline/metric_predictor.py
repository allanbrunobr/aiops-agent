from __future__ import annotations
"""
Módulo de predição de métricas: cálculo de slope EWMA e projeção ETA.

Usa extensão Rust (_aiops_rust) se disponível; caso contrário,
executa implementação Python pura.
"""

from data.models import MetricPoint, MetricSeries

try:
    import _aiops_rust as _rust
    _RUST_AVAILABLE = True
except ImportError:
    _rust = None
    _RUST_AVAILABLE = False


def compute_ewma_slope(
    points: list[MetricPoint],
    span: int = 10,
    threshold: float | None = None,
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

    Retorna
    -------
    (slope, eta_minutes)
        slope: inclinação entre os dois últimos valores EWMA.
        eta_minutes: minutos até o limiar ou None se slope <= 0 ou sem limiar.
    """
    if len(points) < 2:
        return (0.0, None)

    values = [p.value for p in points]

    if _RUST_AVAILABLE:
        result = _rust.compute_ewma_slope(values, span)
        slope: float = result["slope"]
    else:
        slope = _python_ewma_slope(values, span)

    eta_minutes: float | None = None
    if slope > 0 and threshold is not None:
        current_value = values[-1]
        if threshold > current_value:
            eta_minutes = (threshold - current_value) / slope

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
