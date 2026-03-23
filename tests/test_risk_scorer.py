from __future__ import annotations
"""Testes unitários para pipeline.risk_scorer."""

import pytest

from pipeline.log_predictor import LogBurstSignal
from pipeline.trace_predictor import LatencyDriftSignal
from pipeline.risk_scorer import (
    MetricTrendSignal,
    _score_metric_signal,
    _score_log_signal,
    _score_latency_signal,
    compute_risk_score,
    get_event_level,
)


# ---------------------------------------------------------------------------
# _score_metric_signal
# ---------------------------------------------------------------------------

def test_score_metric_none():
    assert _score_metric_signal(None) == 0.0


def test_score_metric_change_point():
    s = MetricTrendSignal(slope=0.0, change_point_detected=True)
    assert _score_metric_signal(s) == 80.0


def test_score_metric_high_slope():
    s = MetricTrendSignal(slope=0.6)
    assert _score_metric_signal(s) == 60.0


def test_score_metric_medium_slope():
    s = MetricTrendSignal(slope=0.2)
    assert _score_metric_signal(s) == 30.0


def test_score_metric_low_slope():
    # Slope baixo (< 0.1) não é sinal de risco — sistema operando normalmente
    s = MetricTrendSignal(slope=0.05)
    assert _score_metric_signal(s) == 0.0


# ---------------------------------------------------------------------------
# _score_log_signal
# ---------------------------------------------------------------------------

def test_score_log_none():
    assert _score_log_signal(None) == 0.0


def test_score_log_no_burst():
    s = LogBurstSignal(burst_detected=False, rate_ratio=1.0, dominant_template=None)
    assert _score_log_signal(s) == 0.0


def test_score_log_burst_only():
    # rate_ratio=3.0 → (3.0 - 1.0) * 50 = 100
    s = LogBurstSignal(burst_detected=True, rate_ratio=3.0, dominant_template=None)
    assert _score_log_signal(s) == 100.0


def test_score_log_burst_with_novel_templates():
    # rate_ratio=2.0 → (2.0 - 1.0) * 50 = 50, +20 = 70
    s = LogBurstSignal(burst_detected=True, rate_ratio=2.0, dominant_template=None,
                       novel_templates=["tpl_new"])
    assert _score_log_signal(s) == 70.0


def test_score_log_burst_capped():
    # rate_ratio=10.0 → (10.0 - 1.0) * 50 = 450, capped at 100 + 20 = capped at 100
    s = LogBurstSignal(burst_detected=True, rate_ratio=10.0, dominant_template=None,
                       novel_templates=["tpl_new"])
    assert _score_log_signal(s) == 100.0


def test_score_log_no_burst_with_novel():
    # novel_templates mas sem burst → base=0 + 20 = 20
    s = LogBurstSignal(burst_detected=False, rate_ratio=1.0, dominant_template=None,
                       novel_templates=["tpl_new"])
    assert _score_log_signal(s) == 20.0


# ---------------------------------------------------------------------------
# _score_latency_signal
# ---------------------------------------------------------------------------

def test_score_latency_none():
    assert _score_latency_signal(None) == 0.0


def test_score_latency_no_drift():
    s = LatencyDriftSignal(drift_detected=False, p95_ratio=1.5, p99_ratio=1.5)
    assert _score_latency_signal(s) == 0.0


def test_score_latency_drift():
    # max(1.5, 1.3) = 1.5 → (1.5 - 1.0) * 200 = 100
    s = LatencyDriftSignal(drift_detected=True, p95_ratio=1.5, p99_ratio=1.3)
    assert _score_latency_signal(s) == 100.0


def test_score_latency_small_drift():
    # max(1.2, 1.1) = 1.2 → (1.2 - 1.0) * 200 = 40
    s = LatencyDriftSignal(drift_detected=True, p95_ratio=1.2, p99_ratio=1.1)
    assert _score_latency_signal(s) == pytest.approx(40.0)


def test_score_latency_capped():
    # ratio=2.0 → (2.0 - 1.0) * 200 = 200, capped at 100
    s = LatencyDriftSignal(drift_detected=True, p95_ratio=2.0, p99_ratio=2.0)
    assert _score_latency_signal(s) == 100.0


# ---------------------------------------------------------------------------
# compute_risk_score
# ---------------------------------------------------------------------------

def test_compute_risk_all_none():
    assert compute_risk_score(None, None, None) == 0.0


def test_compute_risk_all_max():
    # metric: change_point=True → 80.0; log: rate_ratio=10 → 100.0; latency: ratio=2.0 → 100.0
    # weighted = 0.4*80 + 0.3*100 + 0.3*100 = 32 + 30 + 30 = 92
    metric = MetricTrendSignal(slope=0.0, change_point_detected=True)
    log_s = LogBurstSignal(burst_detected=True, rate_ratio=10.0, dominant_template=None)
    lat_s = LatencyDriftSignal(drift_detected=True, p95_ratio=2.0, p99_ratio=2.0)
    score = compute_risk_score(metric, log_s, lat_s)
    assert 0.0 <= score <= 100.0
    assert score == pytest.approx(92.0)


def test_compute_risk_partial_signals():
    # Only metric signal (slope=0.6 → 60.0), others None
    # weighted = 0.4*60 + 0 + 0 = 24.0
    metric = MetricTrendSignal(slope=0.6)
    score = compute_risk_score(metric, None, None)
    assert score == pytest.approx(24.0)


def test_compute_risk_bounded():
    metric = MetricTrendSignal(slope=100.0, change_point_detected=True)
    log_s = LogBurstSignal(burst_detected=True, rate_ratio=100.0, dominant_template=None)
    lat_s = LatencyDriftSignal(drift_detected=True, p95_ratio=100.0, p99_ratio=100.0)
    score = compute_risk_score(metric, log_s, lat_s)
    assert score <= 100.0
    assert score >= 0.0


# ---------------------------------------------------------------------------
# get_event_level
# ---------------------------------------------------------------------------

def test_event_level_alert():
    assert get_event_level(60.0) == "prediction.alert"
    assert get_event_level(100.0) == "prediction.alert"
    assert get_event_level(75.5) == "prediction.alert"


def test_event_level_warning():
    assert get_event_level(30.0) == "prediction.warning"
    assert get_event_level(59.9) == "prediction.warning"
    assert get_event_level(45.0) == "prediction.warning"


def test_event_level_none():
    assert get_event_level(0.0) is None
    assert get_event_level(29.9) is None
    assert get_event_level(10.0) is None
