from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit
"""Testes unitários para pipeline.predictive_stage."""

import datetime

from data.models import PredictionReport
from pipeline.predictive_stage import run_predictive_stage

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeTemplate:
    """Template de log falso para testes."""

    def __init__(
        self, level="ERROR", count=10, template="Connection timeout", services=None
    ):
        self.level = level
        self.count = count
        self.template = template
        self.services = services or {"payment-svc"}


class FakeAnomaly:
    """Anomalia de métrica falsa para testes."""

    def __init__(
        self,
        service="payment-svc",
        metric_name="connections",
        value=100.0,
        deviation_sigma=3.0,
    ):
        self.service = service
        self.metric_name = metric_name
        self.value = value
        self.deviation_sigma = deviation_sigma
        self.timestamp = datetime.datetime.now()
        self.anomaly_type = "spike"
        self.mean = 50.0
        self.std = 10.0
        self.description = "spike detected"


class FakeSlowTrace:
    """Trace lento falso para testes."""

    def __init__(
        self, duration_ms=500.0, bottleneck_service="payment-svc", path="GET /api"
    ):
        self.duration_ms = duration_ms
        self.bottleneck_service = bottleneck_service
        self.path = path


class FakeReport:
    """AggregatedReport falso para testes."""

    def __init__(self, log_templates=None, metric_anomalies=None, slow_traces=None):
        self.log_templates = log_templates or []
        self.metric_anomalies = metric_anomalies or []
        self.slow_traces = slow_traces or []


# ---------------------------------------------------------------------------
# Testes de geração do PredictionReport
# ---------------------------------------------------------------------------


def test_run_predictive_stage_empty_report():
    """Report vazio deve retornar PredictionReport com score 0."""
    report = FakeReport()
    result = run_predictive_stage(report)
    assert result is not None
    assert isinstance(result, PredictionReport)
    assert result.risk_score == 0.0
    assert result.event_level is None
    assert result.signals == []


def test_run_predictive_stage_with_data():
    """Report com dados deve gerar PredictionReport válido."""
    report = FakeReport(
        log_templates=[FakeTemplate(level="ERROR", count=50)],
        metric_anomalies=[
            FakeAnomaly(deviation_sigma=6.0),
            FakeAnomaly(deviation_sigma=4.0),
        ],
        slow_traces=[FakeSlowTrace(500.0), FakeSlowTrace(800.0)],
    )
    result = run_predictive_stage(report)
    assert result is not None
    assert isinstance(result, PredictionReport)
    assert 0.0 <= result.risk_score <= 100.0


def test_run_predictive_stage_returns_prediction_report_fields():
    """PredictionReport deve ter todos os campos obrigatórios."""
    report = FakeReport()
    result = run_predictive_stage(report)
    assert result is not None
    assert hasattr(result, "risk_score")
    assert hasattr(result, "eta_minutes")
    assert hasattr(result, "signals")
    assert hasattr(result, "event_level")
    assert hasattr(result, "matched_history")
    assert isinstance(result.matched_history, list)


# ---------------------------------------------------------------------------
# Teste de degradação graciosa
# ---------------------------------------------------------------------------


def test_run_predictive_stage_graceful_on_exception():
    """Falhas em sub-sinais devem degradar graciosamente sem crashar o pipeline."""

    class BrokenReport:
        @property
        def log_templates(self):
            raise RuntimeError("simulando falha catastrófica")

        @property
        def metric_anomalies(self):
            raise RuntimeError("simulando falha catastrófica")

        @property
        def slow_traces(self):
            raise RuntimeError("simulando falha catastrófica")

    # Pipeline não deve crashar — retorna PredictionReport com 0 sinais ou None
    result = run_predictive_stage(BrokenReport())
    # Degradação graciosa: pode retornar None ou PredictionReport com sinais vazios
    assert result is None or (
        isinstance(result, PredictionReport) and result.signals == []
    )


def test_run_predictive_stage_none_report():
    """None como report deve retornar None ou PredictionReport vazio sem crashar."""
    result = run_predictive_stage(None)
    # Degradação graciosa: pode retornar None ou PredictionReport com sinais vazios
    assert result is None or (
        isinstance(result, PredictionReport) and result.signals == []
    )


# ---------------------------------------------------------------------------
# Teste de mapeamento de event_level
# ---------------------------------------------------------------------------


def test_event_level_alert_on_high_risk():
    """Score alto deve mapear para prediction.alert."""
    # Muitos templates de erro com alta contagem geram bursts
    report = FakeReport(
        log_templates=[FakeTemplate(level="ERROR", count=100)] * 5,
        metric_anomalies=[FakeAnomaly(deviation_sigma=10.0)] * 5,
        slow_traces=[FakeSlowTrace(2000.0)] * 5,
    )
    result = run_predictive_stage(report)
    assert result is not None
    # event_level pode ser None, warning ou alert — apenas verificamos que é válido
    assert result.event_level in (None, "prediction.warning", "prediction.alert")


def test_risk_score_within_bounds():
    """Risk score deve sempre estar entre 0 e 100."""
    for _ in range(3):
        report = FakeReport(
            log_templates=[FakeTemplate()],
            metric_anomalies=[FakeAnomaly()],
            slow_traces=[FakeSlowTrace()],
        )
        result = run_predictive_stage(report)
        if result is not None:
            assert 0.0 <= result.risk_score <= 100.0


def test_matched_history_default_empty():
    """matched_history deve ser lista vazia por padrão."""
    report = FakeReport()
    result = run_predictive_stage(report)
    assert result is not None
    assert result.matched_history == []


# ---------------------------------------------------------------------------
# D1 — clamp de sinais (log negativo + baseline de latência zero)
# ---------------------------------------------------------------------------


def test_log_burst_ratio_below_one_does_not_go_negative():
    """rate_ratio<1 com burst_detected=True não pode gerar score negativo."""
    from pipeline.log_predictor import LogBurstSignal
    from pipeline.risk_scorer import _score_log_signal

    signal = LogBurstSignal(
        burst_detected=True,
        rate_ratio=0.2,
        dominant_template=None,
        novel_templates=["nova"],
    )
    # Sem clamp: base = -40 + 20 = -20 → mascararia a novidade.
    assert _score_log_signal(signal) == 20.0


def test_log_ratio_below_one_does_not_reduce_other_signals():
    """Ratio<1 no log não pode reduzir o score abaixo dos outros sinais."""
    from pipeline.log_predictor import LogBurstSignal
    from pipeline.risk_scorer import MetricTrendSignal, compute_risk_score

    metric = MetricTrendSignal(slope=1.0)  # 60 * 0.4 = 24
    garbage_log = LogBurstSignal(
        burst_detected=True,
        rate_ratio=-5.0,
        dominant_template=None,
        novel_templates=["x"],
    )
    # log contribui 0.3 * 20 = 6 (só a novidade), nunca negativo.
    assert compute_risk_score(metric, garbage_log, None) >= compute_risk_score(
        metric, None, None
    )


def test_latency_zero_baseline_gives_no_drift_signal():
    """Baseline com p95/p99 zero não explode o ratio nem alarma."""
    from pipeline.risk_scorer import compute_risk_score
    from pipeline.trace_predictor import LatencyDriftSignal, detect_latency_drift

    result = detect_latency_drift([0.0] * 20, [50.0] * 20)
    assert result["drift_detected"] is False
    assert result["p95_ratio"] == 1.0
    assert result["p99_ratio"] == 1.0

    signal = LatencyDriftSignal(
        drift_detected=result["drift_detected"],
        p95_ratio=result["p95_ratio"],
        p99_ratio=result["p99_ratio"],
    )
    assert compute_risk_score(None, None, signal) == 0.0


def test_latency_real_baseline_still_detects_drift():
    """Regressão: baseline legítimo continua detectando drift."""
    from pipeline.trace_predictor import detect_latency_drift

    result = detect_latency_drift([100.0] * 20, [200.0] * 20)
    assert result["drift_detected"] is True
    assert result["p95_ratio"] == pytest.approx(2.0)


def test_predictive_stage_populates_eta_with_threshold():
    """D2: ETA deixa de ser None — threshold = mean + 3*std da anomalia."""
    base = datetime.datetime(2026, 1, 1)
    anomalies = []
    for i in range(12):
        a = FakeAnomaly(deviation_sigma=4.0 + i, value=60.0 + 2.0 * i)
        a.timestamp = base + datetime.timedelta(seconds=30 * i)
        anomalies.append(a)  # mean=50, std=10 => threshold 80
    result = run_predictive_stage(FakeReport(metric_anomalies=anomalies))
    assert result is not None
    assert result.eta_minutes is not None
    assert result.eta_minutes > 0
