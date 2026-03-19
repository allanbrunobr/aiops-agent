"""
Testes unitários para pipeline/trace_predictor.py.
"""
from __future__ import annotations

import datetime

import pytest

from data.models import Span
from pipeline.trace_predictor import (
    LatencyDriftSignal,
    _percentile,
    analyze_trace_signals,
    detect_latency_drift,
)

_TS = datetime.datetime(2026, 3, 4, 14, 0, 0)
_TD = datetime.timedelta(seconds=1)


def _make_span(
    duration_ms: float,
    parent_span_id: str | None = None,
    offset_secs: int = 0,
) -> Span:
    """Cria um Span de teste."""
    return Span(
        trace_id="trace-1",
        span_id=f"span-{offset_secs}",
        parent_span_id=parent_span_id,
        service="svc",
        operation="op",
        start_time=_TS + datetime.timedelta(seconds=offset_secs),
        duration_ms=duration_ms,
        status="OK",
    )


# ---------------------------------------------------------------------------
# _percentile
# ---------------------------------------------------------------------------


def test_percentile_median():
    """Mediana de lista ímpar deve ser o valor central."""
    assert _percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == pytest.approx(3.0)


def test_percentile_p95_simple():
    """p95 de 100 valores uniformes deve ser próximo ao máximo."""
    values = list(range(1, 101))
    p95 = _percentile(values, 95)
    assert p95 >= 95.0


def test_percentile_empty():
    """Lista vazia deve retornar 0.0 sem exceção."""
    assert _percentile([], 95) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# detect_latency_drift
# ---------------------------------------------------------------------------


def test_no_drift_flat():
    """Latências idênticas não devem gerar drift."""
    flat = [100.0] * 50
    result = detect_latency_drift(flat, flat)
    assert result["drift_detected"] is False
    assert result["p95_ratio"] == pytest.approx(1.0)


def test_drift_50pct_increase():
    """Aumento de 50% no p95 deve disparar drift com threshold=0.20."""
    baseline = [100.0] * 50
    current = [150.0] * 50  # 50% acima do baseline
    result = detect_latency_drift(baseline, current)
    assert result["drift_detected"] is True
    assert result["p95_ratio"] == pytest.approx(1.5, rel=0.01)


def test_drift_below_threshold():
    """Aumento de 10% não deve disparar drift com threshold=0.20."""
    baseline = [100.0] * 50
    current = [110.0] * 50
    result = detect_latency_drift(baseline, current, threshold_pct=0.20)
    assert result["drift_detected"] is False


def test_drift_empty_lists():
    """Listas vazias não devem lançar exceção e drift_detected deve ser False."""
    result = detect_latency_drift([], [])
    assert result["drift_detected"] is False
    assert result["p95_ratio"] == pytest.approx(0.0)
    assert result["p99_ratio"] == pytest.approx(0.0)


def test_drift_empty_baseline():
    """Baseline vazio com current não-vazio: ratio alto por divisão por 0.001."""
    result = detect_latency_drift([], [100.0] * 10)
    assert result["drift_detected"] is True


# ---------------------------------------------------------------------------
# analyze_trace_signals
# ---------------------------------------------------------------------------


def test_analyze_no_drift_flat_spans():
    """Spans com latência uniforme não devem gerar drift."""
    spans = [_make_span(100.0, offset_secs=i) for i in range(20)]
    signal = analyze_trace_signals(spans)
    assert isinstance(signal, LatencyDriftSignal)
    assert signal.drift_detected is False


def test_analyze_ignores_child_spans():
    """Spans filhos devem ser ignorados; só spans raiz são considerados."""
    # 10 root spans com 100ms + 10 child spans com 9999ms
    root_spans = [_make_span(100.0, parent_span_id=None, offset_secs=i) for i in range(10)]
    child_spans = [_make_span(9999.0, parent_span_id="parent", offset_secs=i + 100) for i in range(10)]
    signal = analyze_trace_signals(root_spans + child_spans)
    # Apenas root spans são usados — latência uniforme, sem drift
    assert signal.drift_detected is False


def test_analyze_empty_spans():
    """Lista de spans vazia não deve lançar exceção."""
    signal = analyze_trace_signals([])
    assert signal.drift_detected is False


def test_analyze_sample_incident_drift():
    """Incidente simulado (degradação 14:19→14:23) deve detectar drift."""
    from data.sample_incident import generate_traces

    spans = generate_traces()
    signal = analyze_trace_signals(spans)
    assert signal.drift_detected is True
    assert signal.p95_ratio > 1.0
