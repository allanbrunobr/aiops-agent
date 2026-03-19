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
            service="svc", metric_name="cpu", points=pts,
            threshold=90.0, eta_minutes=8.0,
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
            # eta = (threshold - current_value) / slope
            expected = (10.0 - 2.0) / slope
            assert abs(eta - expected) < 1e-9
