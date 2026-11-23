from __future__ import annotations

"""Testes unitários para MetricSeries e compute_ewma_slope."""

import datetime

import pytest

from data.models import MetricPoint, MetricSeries
from pipeline.metric_predictor import compute_ewma_slope


def _make_point(value: float, minute: int = 0) -> MetricPoint:
    """Cria um MetricPoint auxiliar para testes."""
    ts = datetime.datetime(2026, 1, 1, 0, minute % 60, 0)
    return MetricPoint(timestamp=ts, service="svc", metric_name="cpu", value=value)


# ── Testes de MetricSeries ────────────────────────────────────────────────────


@pytest.mark.unit
class TestMetricSeriesInstantiation:
    """Verifica a instanciação do dataclass MetricSeries."""

    def test_basic_fields(self):
        pts = [_make_point(1.0, i) for i in range(5)]
        ms = MetricSeries(service="svc", metric_name="cpu", points=pts)
        assert ms.service == "svc"
        assert ms.metric_name == "cpu"
        assert len(ms.points) == 5
        assert ms.threshold is None
        assert ms.eta_minutes is None

    def test_with_threshold(self):
        pts = [_make_point(1.0, i) for i in range(3)]
        ms = MetricSeries(service="svc", metric_name="cpu", points=pts, threshold=90.0)
        assert ms.threshold == 90.0
        assert ms.eta_minutes is None

    def test_with_eta_minutes(self):
        pts = [_make_point(50.0, i) for i in range(3)]
        ms = MetricSeries(
            service="svc",
            metric_name="cpu",
            points=pts,
            threshold=90.0,
            eta_minutes=8.0,
        )
        assert ms.eta_minutes == 8.0


# ── Testes de compute_ewma_slope ──────────────────────────────────────────────


@pytest.mark.unit
class TestComputeEwmaSlope:
    """Verifica o cálculo de slope EWMA e projeção ETA."""

    def test_empty_points_returns_zero(self):
        slope, eta = compute_ewma_slope([], span=10)
        assert slope == 0.0
        assert eta is None

    def test_single_point_returns_zero(self):
        pts = [_make_point(10.0)]
        slope, eta = compute_ewma_slope(pts, span=10)
        assert slope == 0.0
        assert eta is None

    def test_flat_series_slope_near_zero(self):
        pts = [_make_point(50.0, i) for i in range(20)]
        slope, eta = compute_ewma_slope(pts, span=10)
        assert abs(slope) < 0.01

    def test_monotonic_increase_positive_slope(self):
        pts = [_make_point(float(i), i) for i in range(1, 21)]
        slope, _ = compute_ewma_slope(pts, span=10)
        assert slope > 0

    def test_monotonic_increase_computes_eta(self):
        pts = [_make_point(float(i * 5), i) for i in range(1, 21)]
        slope, eta = compute_ewma_slope(pts, span=10, threshold=200.0)
        assert slope > 0
        assert eta is not None
        assert eta > 0

    def test_no_threshold_no_eta(self):
        pts = [_make_point(float(i), i) for i in range(20)]
        _, eta = compute_ewma_slope(pts, span=10)
        assert eta is None

    def test_slope_le_zero_no_eta(self):
        # Série decrescente — slope < 0, eta deve ser None
        pts = [_make_point(float(20 - i), i) for i in range(20)]
        slope, eta = compute_ewma_slope(pts, span=10, threshold=50.0)
        assert slope < 0
        assert eta is None

    def test_eta_formula(self):
        # Série com slope conhecido: todos os valores = 1.0 exceto o último = 2.0
        pts = [_make_point(1.0, i) for i in range(10)] + [_make_point(2.0, 10)]
        slope, eta = compute_ewma_slope(pts, span=10, threshold=10.0)
        if slope > 0:
            assert eta is not None
            # eta = (threshold - current_ewma) / slope * intervalo(min); 1 min/amostra
            expected = (10.0 - _ewma_last([1.0] * 10 + [2.0], 10)) / slope
            assert abs(eta - expected) < 1e-9


# ── D2: ETA em minutos (sample_interval + EWMA current) ───────────────────────


def _linear_points(n: int, step_seconds: float, slope_per_sample: float):
    t0 = datetime.datetime(2026, 1, 1)
    return [
        MetricPoint(
            timestamp=t0 + datetime.timedelta(seconds=i * step_seconds),
            service="svc",
            metric_name="mem",
            value=100.0 + i * slope_per_sample,
        )
        for i in range(n)
    ]


def _ewma_last(values: list[float], span: int) -> float:
    alpha = 2.0 / (span + 1)
    last = values[0]
    for v in values[1:]:
        last = alpha * v + (1.0 - alpha) * last
    return last


@pytest.mark.unit
class TestEtaSampleInterval:
    def test_explicit_interval_scales_eta_to_minutes(self):
        pts = _linear_points(60, 30.0, 2.0)  # +2/amostra, amostra a cada 0.5 min
        slope, eta = compute_ewma_slope(
            pts, span=10, threshold=400.0, sample_interval_minutes=0.5
        )
        current = _ewma_last([p.value for p in pts], 10)
        assert eta == pytest.approx((400.0 - current) / slope * 0.5)

    def test_interval_derived_from_timestamps(self):
        pts = _linear_points(60, 30.0, 2.0)
        _, eta_derived = compute_ewma_slope(pts, span=10, threshold=400.0)
        _, eta_explicit = compute_ewma_slope(
            pts, span=10, threshold=400.0, sample_interval_minutes=0.5
        )
        assert eta_derived == pytest.approx(eta_explicit)

    def test_eta_in_real_minutes_linear_series(self):
        # Linear: 2 un/amostra a cada 30 s = 4 un/min. Valor final 218, EWMA
        # defasada ~9 amostras; limiar 400 => ~ (400-200)/4 = 50 min.
        pts = _linear_points(60, 30.0, 2.0)
        _, eta = compute_ewma_slope(pts, span=10, threshold=400.0)
        assert eta is not None
        assert 45.0 <= eta <= 55.0

    def test_threshold_below_ewma_current_no_eta(self):
        pts = _linear_points(30, 60.0, 1.0)
        _, eta = compute_ewma_slope(pts, span=10, threshold=50.0)
        assert eta is None

    def test_invalid_interval_rejected(self):
        pts = _linear_points(5, 60.0, 1.0)
        with pytest.raises(ValueError):
            compute_ewma_slope(pts, span=10, threshold=10.0, sample_interval_minutes=0)
