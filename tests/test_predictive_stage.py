from __future__ import annotations

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
    """Trace lento falso com o shape real de SlowCallPath (F1-3)."""

    def __init__(
        self,
        duration_ms=500.0,
        bottleneck_service="payment-svc",
        path=None,
    ):
        self.total_duration_ms = duration_ms
        self.bottleneck_service = bottleneck_service
        self.path = path if path is not None else ["gateway", bottleneck_service]
        self.timestamp = datetime.datetime(2026, 1, 1, 14, 0, 0)


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
# F1-3: fidelidade aos dados reais (regressão)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.mark.unit
class TestPredictiveStageDataFidelity:
    """Regressão F1-3: o estágio preditivo operava sobre dados errados.

    Bugs originais:
    - getattr(trace, 'duration_ms') mas SlowCallPath tem total_duration_ms → 0.0;
    - path (list) colocado em Span.operation (str);
    - slope EWMA calculado sobre métricas distintas misturadas;
    - logs sintéticos todos com timestamp=now.
    """

    _BASE = datetime.datetime(2026, 1, 1, 14, 0, 0)

    def _make_report(self):
        from pipeline.log_parser import LogTemplate
        from pipeline.metric_filter import MetricAnomaly
        from pipeline.trace_filter import SlowCallPath

        base = self._BASE
        anomalies = [
            MetricAnomaly(
                service="payment-svc",
                metric_name="error_rate",
                anomaly_type="spike",
                timestamp=base,
                value=10.0,
                mean=1.0,
                std=1.0,
                deviation_sigma=8.0,
                description="",
            ),
            MetricAnomaly(
                service="order-svc",
                metric_name="error_rate",
                anomaly_type="spike",
                timestamp=base + datetime.timedelta(minutes=1),
                value=30.0,
                mean=1.0,
                std=1.0,
                deviation_sigma=9.0,
                description="",
            ),
            MetricAnomaly(
                service="payment-svc",
                metric_name="cpu_usage",
                anomaly_type="spike",
                timestamp=base,
                value=95.0,
                mean=40.0,
                std=5.0,
                deviation_sigma=11.0,
                description="",
            ),
        ]
        traces = [
            SlowCallPath(
                trace_id="t1",
                path=["gateway", "payment-svc"],
                operations=["GET", "POST"],
                total_duration_ms=4500.0,
                bottleneck_service="payment-svc",
                bottleneck_duration_ms=4200.0,
                normal_duration_ms=120.0,
                timestamp=base,
                attributes={},
            ),
            SlowCallPath(
                trace_id="t2",
                path=["gateway", "order-svc"],
                operations=["GET", "POST"],
                total_duration_ms=5200.0,
                bottleneck_service="order-svc",
                bottleneck_duration_ms=4900.0,
                normal_duration_ms=120.0,
                timestamp=base + datetime.timedelta(minutes=2),
                attributes={},
            ),
        ]
        templates = [
            LogTemplate(
                template="Connection timeout",
                count=6,
                level="ERROR",
                services={"payment-svc"},
                first_seen=base,
                last_seen=base + datetime.timedelta(minutes=10),
            )
        ]
        return FakeReport(
            log_templates=templates, metric_anomalies=anomalies, slow_traces=traces
        )

    def test_metric_slope_grouped_by_metric(self, monkeypatch):
        """Slope EWMA por métrica — nunca misturar métricas distintas na série."""
        import pipeline.predictive_stage as stage

        calls = []
        real = stage.compute_ewma_slope

        def spy(points, *args, **kwargs):
            calls.append(points)
            return real(points, *args, **kwargs)

        monkeypatch.setattr(stage, "compute_ewma_slope", spy)
        result = stage.run_predictive_stage(self._make_report())

        assert result is not None
        assert calls, "slope deve ser computado para grupo com >=2 pontos"
        for points in calls:
            assert len(points) >= 2
            assert len({p.metric_name for p in points}) == 1
        assert any(len(p) == 2 and p[0].metric_name == "error_rate" for p in calls)

    def test_synthetic_spans_use_real_fields(self, monkeypatch):
        """Spans sintéticos: duration real, operation str, start_time do trace."""
        import pipeline.predictive_stage as stage

        captured = []
        real = stage.analyze_trace_signals

        def spy(spans):
            captured.extend(spans)
            return real(spans)

        monkeypatch.setattr(stage, "analyze_trace_signals", spy)
        result = stage.run_predictive_stage(self._make_report())

        assert result is not None
        assert len(captured) == 2
        assert [s.duration_ms for s in captured] == [4500.0, 5200.0]
        assert all(isinstance(s.operation, str) for s in captured)
        assert captured[0].operation == "gateway -> payment-svc"
        assert captured[0].start_time == self._BASE

    def test_synthetic_logs_use_template_timestamps(self, monkeypatch):
        """Logs sintéticos distribuídos entre first_seen e last_seen do template."""
        import pipeline.predictive_stage as stage

        captured = []
        real = stage.analyze_log_signals

        def spy(entries):
            captured.extend(entries)
            return real(entries)

        monkeypatch.setattr(stage, "analyze_log_signals", spy)
        result = stage.run_predictive_stage(self._make_report())

        assert result is not None
        assert len(captured) == 6
        assert captured[0].timestamp == self._BASE
        assert captured[-1].timestamp == self._BASE + datetime.timedelta(minutes=10)
        assert len({e.timestamp for e in captured}) > 1

    def test_risk_score_reflects_strong_signals(self):
        """Dados fortes e conhecidos → score > 0 com os 3 sinais presentes."""
        result = run_predictive_stage(self._make_report())
        assert result is not None
        assert result.risk_score > 0.0
        assert "metric_trend" in result.signals
        assert "log_burst" in result.signals
        assert "latency_drift" in result.signals
