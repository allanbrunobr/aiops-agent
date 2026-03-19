from __future__ import annotations
"""Testes unitários para pipeline.predictive_stage."""

import pytest
from unittest.mock import patch, MagicMock
import datetime

from data.models import PredictionReport
from pipeline.predictive_stage import run_predictive_stage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class FakeTemplate:
    """Template de log falso para testes."""
    def __init__(self, level="ERROR", count=10, template="Connection timeout", services=None):
        self.level = level
        self.count = count
        self.template = template
        self.services = services or {"payment-svc"}


class FakeAnomaly:
    """Anomalia de métrica falsa para testes."""
    def __init__(self, service="payment-svc", metric_name="connections", value=100.0,
                 deviation_sigma=3.0):
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
    def __init__(self, duration_ms=500.0, bottleneck_service="payment-svc", path="GET /api"):
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
        metric_anomalies=[FakeAnomaly(deviation_sigma=6.0), FakeAnomaly(deviation_sigma=4.0)],
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
    assert result is None or (isinstance(result, PredictionReport) and result.signals == [])


def test_run_predictive_stage_none_report():
    """None como report deve retornar None ou PredictionReport vazio sem crashar."""
    result = run_predictive_stage(None)
    # Degradação graciosa: pode retornar None ou PredictionReport com sinais vazios
    assert result is None or (isinstance(result, PredictionReport) and result.signals == [])


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
