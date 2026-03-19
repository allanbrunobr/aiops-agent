from __future__ import annotations
"""
Estágio preditivo do pipeline AIOps.

Combina sinais de MetricTrendSignal, LogBurstSignal e LatencyDriftSignal
em um PredictionReport com score de risco e nível de evento de lifecycle.
Todas as exceções são capturadas — o pipeline nunca deve crashar neste estágio.
"""

import logging

from data.models import PredictionReport
from pipeline.log_predictor import LogBurstSignal, analyze_log_signals
from pipeline.trace_predictor import LatencyDriftSignal, analyze_trace_signals
from pipeline.metric_predictor import compute_ewma_slope
from pipeline.risk_scorer import (
    MetricTrendSignal,
    compute_risk_score,
    get_event_level,
)

logger = logging.getLogger(__name__)


def run_predictive_stage(aggregated_report) -> PredictionReport | None:
    """Executa o estágio preditivo e retorna PredictionReport ou None em caso de erro.

    Extrai pontos de métrica, entradas de log e spans do aggregated_report,
    computa sinais preditivos e agrega em score de risco unificado.

    Parâmetros
    ----------
    aggregated_report : AggregatedReport
        Relatório agregado com dados de observabilidade processados.

    Retorna
    -------
    PredictionReport ou None se qualquer exceção ocorrer.
    """
    try:
        # Extrai dados do aggregated_report
        metric_signal: MetricTrendSignal | None = None
        log_signal: LogBurstSignal | None = None
        latency_signal: LatencyDriftSignal | None = None

        signals: list[str] = []
        eta_minutes: float | None = None

        # Sinal de métrica: usa anomalias para construir pontos sintéticos de slope
        try:
            metric_anomalies = getattr(aggregated_report, "metric_anomalies", [])
            if metric_anomalies:
                # Usa os valores das anomalias como proxy de série temporal
                from data.models import MetricPoint
                import datetime
                now = datetime.datetime.now()
                points = [
                    MetricPoint(
                        timestamp=getattr(a, "timestamp", now),
                        service=getattr(a, "service", "unknown"),
                        metric_name=getattr(a, "metric_name", "metric"),
                        value=getattr(a, "value", 0.0),
                    )
                    for a in metric_anomalies
                ]
                if len(points) >= 2:
                    slope, eta = compute_ewma_slope(points)
                    # Detecta change point se desvio sigma alto
                    max_sigma = max(
                        (getattr(a, "deviation_sigma", 0.0) for a in metric_anomalies),
                        default=0.0,
                    )
                    change_point = max_sigma > 5.0
                    metric_signal = MetricTrendSignal(
                        slope=slope,
                        eta_minutes=eta,
                        change_point_detected=change_point,
                    )
                    eta_minutes = eta
                    signals.append("metric_trend")
        except Exception as exc:
            logger.warning("predictive_stage.metric_signal_failed: %s", exc)

        # Sinal de log: usa log_templates para reconstruir entradas sintéticas
        try:
            log_templates = getattr(aggregated_report, "log_templates", [])
            if log_templates:
                # Constrói entradas de log sintéticas a partir dos templates
                from data.models import LogEntry
                import datetime
                now = datetime.datetime.now()
                log_entries = []
                for tpl in log_templates:
                    count = getattr(tpl, "count", 1)
                    for _ in range(min(count, 10)):  # limita para evitar explosion
                        log_entries.append(LogEntry(
                            timestamp=now,
                            level=getattr(tpl, "level", "INFO"),
                            service=next(iter(getattr(tpl, "services", {"unknown"})), "unknown"),
                            message=str(getattr(tpl, "template", "")),
                        ))
                if len(log_entries) >= 2:
                    log_signal = analyze_log_signals(log_entries)
                    signals.append("log_burst")
        except Exception as exc:
            logger.warning("predictive_stage.log_signal_failed: %s", exc)

        # Sinal de latência: usa slow_traces para construir spans sintéticos
        try:
            slow_traces = getattr(aggregated_report, "slow_traces", [])
            if slow_traces:
                from data.models import Span
                import datetime
                import uuid
                now = datetime.datetime.now()
                spans = []
                for trace in slow_traces:
                    duration = getattr(trace, "duration_ms", 0.0)
                    spans.append(Span(
                        trace_id=str(uuid.uuid4()),
                        span_id=str(uuid.uuid4()),
                        parent_span_id=None,
                        service=getattr(trace, "bottleneck_service", "unknown"),
                        operation=getattr(trace, "path", "unknown"),
                        start_time=now,
                        duration_ms=duration,
                        status="ok",
                    ))
                if len(spans) >= 2:
                    latency_signal = analyze_trace_signals(spans)
                    signals.append("latency_drift")
        except Exception as exc:
            logger.warning("predictive_stage.latency_signal_failed: %s", exc)

        # Calcula score de risco unificado
        risk_score = compute_risk_score(metric_signal, log_signal, latency_signal)
        event_level = get_event_level(risk_score)

        return PredictionReport(
            risk_score=risk_score,
            eta_minutes=eta_minutes,
            signals=signals,
            event_level=event_level,
        )

    except Exception as exc:
        logger.warning("predictive_stage.failed — returning None: %s", exc)
        return None
