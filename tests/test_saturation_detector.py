from __future__ import annotations

"""Testes do detector de saturação HPA (D4): platô no teto."""

import datetime

import pytest

from pipeline.predictive_stage import run_predictive_stage
from pipeline.risk_scorer import (
    MetricTrendSignal,
    compute_risk_score,
    get_event_level,
)
from pipeline.saturation_detector import SaturationSignal, detect_saturation
from pipeline.trace_predictor import LatencyDriftSignal

pytestmark = pytest.mark.unit

_RAMP_THEN_CEILING = [4, 5, 6, 7, 8, 9, 10, 10, 10, 10, 10, 10]


def test_plateau_at_explicit_ceiling_is_detected():
    sig = detect_saturation(_RAMP_THEN_CEILING, ceiling=10)
    assert isinstance(sig, SaturationSignal)
    assert sig.ceiling == 10
    assert sig.sustained_samples == 6
    assert sig.utilization == pytest.approx(1.0)
    assert sig.ceiling_explicit is True


def test_plateau_below_ceiling_has_no_signal():
    # Platô estável, mas em 70% do teto.
    assert detect_saturation([3, 4, 5, 6, 7, 7, 7, 7, 7, 7], ceiling=10) is None


def test_short_series_has_no_signal():
    assert detect_saturation([10, 10, 10, 10], ceiling=10) is None


def test_short_plateau_after_ramp_has_no_signal():
    # Só 3 amostras no teto (< K=5).
    assert detect_saturation([4, 5, 6, 7, 8, 9, 10, 10, 10], ceiling=10) is None


def test_high_variance_near_top_has_no_signal():
    # Oscila entre 9.6 e 12 em torno do teto: spread > 3% do teto.
    assert detect_saturation([9.6, 12, 9.6, 12, 9.6, 12, 9.6], ceiling=10) is None


def test_plateau_already_left_has_no_signal():
    # Esteve no teto mas a cauda desceu: não está saturado agora.
    assert detect_saturation([10, 10, 10, 10, 10, 10, 8, 6], ceiling=10) is None


def test_inferred_ceiling_requires_prior_rise():
    sig = detect_saturation(_RAMP_THEN_CEILING, ceiling=None)
    assert sig is not None and sig.ceiling_explicit is False
    # Flat sem teto explícito: máximo da janela não é evidência de saturação.
    assert detect_saturation([10.0] * 12, ceiling=None) is None


def test_flat_at_explicit_ceiling_is_saturation():
    assert detect_saturation([10.0] * 8, ceiling=10) is not None


@pytest.mark.parametrize("ceiling", [0, -1, float("nan")])
def test_invalid_explicit_ceiling_falls_back_safely(ceiling):
    # Teto inválido → trata como não informado; flat não vira saturação.
    assert detect_saturation([10.0] * 8, ceiling=ceiling) is None


def test_non_finite_values_are_ignored():
    assert detect_saturation([float("nan")] * 10, ceiling=10) is None


# --- risk_scorer ----------------------------------------------------------


def test_risk_score_without_saturation_is_unchanged():
    m = MetricTrendSignal(slope=1.0)
    assert compute_risk_score(m, None, None) == pytest.approx(24.0)


def test_risk_score_saturation_plus_latency_is_alert():
    sat = detect_saturation(_RAMP_THEN_CEILING, ceiling=10)
    lat = LatencyDriftSignal(drift_detected=True, p95_ratio=2.0, p99_ratio=2.0)
    score = compute_risk_score(None, None, lat, saturation_signal=sat)
    assert get_event_level(score) == "prediction.alert"


def test_risk_score_saturation_alone_is_warning_not_alert():
    sat = detect_saturation(_RAMP_THEN_CEILING, ceiling=10)
    score = compute_risk_score(None, None, None, saturation_signal=sat)
    assert get_event_level(score) == "prediction.warning"


def test_weights_sum_to_one():
    from pipeline.risk_scorer import _WEIGHTS_BASE, _WEIGHTS_WITH_SATURATION

    assert sum(_WEIGHTS_BASE) == pytest.approx(1.0)
    assert sum(_WEIGHTS_WITH_SATURATION) == pytest.approx(1.0)


# --- predictive_stage -----------------------------------------------------

_BASE = datetime.datetime(2026, 1, 1)
_HPA = "kube_hpa_status_current_replicas"


class _Anom:
    def __init__(self, i, value, metric=_HPA):
        self.service = "wbia"
        self.metric_name = metric
        self.value = value
        self.deviation_sigma = 3.0
        self.timestamp = _BASE + datetime.timedelta(minutes=i)
        self.mean = 5.0
        self.std = 1.0


class _Report:
    def __init__(self, anomalies):
        self.metric_anomalies = anomalies
        self.log_templates = []
        self.slow_traces = []


def _series(values):
    return [_Anom(i, v) for i, v in enumerate(values)]


def test_stage_emits_saturation_signal_for_hpa_plateau():
    r = run_predictive_stage(_Report(_series(_RAMP_THEN_CEILING)), ceilings={_HPA: 10})
    assert r is not None
    assert "saturation" in r.signals
    assert r.risk_score > 0


def test_stage_pure_plateau_is_not_lost_by_flat_gate():
    # Platô puro no teto: D3 barra (efeito ~0) mas D4 tem que enxergar.
    r = run_predictive_stage(_Report(_series([10.0] * 10)), ceilings={_HPA: 10})
    assert r is not None
    assert r.signals == ["saturation"]
    assert r.event_level == "prediction.warning"


def test_stage_flat_without_ceiling_has_no_saturation():
    r = run_predictive_stage(_Report(_series([100.0] * 12)))
    assert r is not None
    assert "saturation" not in r.signals
    assert r.risk_score == 0.0


def test_stage_plateau_below_ceiling_has_no_saturation():
    r = run_predictive_stage(
        _Report(_series([3, 4, 5, 6, 7, 7, 7, 7, 7, 7])), ceilings={_HPA: 10}
    )
    assert r is not None
    assert "saturation" not in r.signals


def test_stage_other_metric_ceiling_does_not_apply():
    r = run_predictive_stage(
        _Report(_series([10.0] * 10)), ceilings={"outra_metrica": 10}
    )
    assert r is not None
    assert "saturation" not in r.signals
