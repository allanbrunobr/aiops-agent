from __future__ import annotations

"""
Estágio preditivo do pipeline AIOps.

Combina sinais de MetricTrendSignal, LogBurstSignal e LatencyDriftSignal
em um PredictionReport com score de risco e nível de evento de lifecycle.
Todas as exceções são capturadas — o pipeline nunca deve crashar neste estágio.
"""

import logging
import math

from data.models import PredictionReport
from pipeline.log_predictor import LogBurstSignal, analyze_log_signals
from pipeline.metric_predictor import compute_ewma_slope
from pipeline.risk_scorer import (
    MetricTrendSignal,
    compute_risk_score,
    get_event_level,
    relative_slope,
)
from pipeline.trace_predictor import LatencyDriftSignal, analyze_trace_signals

logger = logging.getLogger(__name__)

# Portões D3 (controle de falso positivo): abaixo disso → sem sinal.
MIN_SERIES_POINTS = 5  # série com menos pontos não estabelece tendência
MIN_RELATIVE_EFFECT = 0.02  # amplitude (max-min)/|média| < 2% = flat/ruído


def _relative_effect(values: list[float]) -> float:
    """Amplitude relativa da série: (max - min) / max(|média|, epsilon)."""
    mean = sum(values) / len(values)
    return (max(values) - min(values)) / max(abs(mean), 1e-9)


def _eta_threshold(anomaly, eta_sigma: float) -> float | None:
    """Limiar de ETA = mean + eta_sigma * std da baseline da anomalia."""
    mean = getattr(anomaly, "mean", None)
    std = getattr(anomaly, "std", None)
    if not isinstance(mean, int | float) or not isinstance(std, int | float):
        return None
    if not (math.isfinite(mean) and math.isfinite(std)) or std <= 0:
        return None
    return float(mean + eta_sigma * std)


def run_predictive_stage(
    aggregated_report,
    *,
    eta_sigma: float = 3.0,
    use_relative_slope: bool = False,
) -> PredictionReport | None:
    """Executa o estágio preditivo e retorna PredictionReport ou None em caso de erro.

    Extrai pontos de métrica, entradas de log e spans do aggregated_report,
    computa sinais preditivos e agrega em score de risco unificado.

    Parâmetros
    ----------
    aggregated_report : AggregatedReport
        Relatório agregado com dados de observabilidade processados.
    eta_sigma : float
        Nº de desvios-padrão acima da média de baseline que define o limiar
        do ETA por métrica (limiar = mean + eta_sigma * std, o mesmo 3-sigma
        da detecção por padrão). Sem mean/std válidos, ETA fica None.
    use_relative_slope : bool
        Se True, o score de métrica usa limiares de slope relativo (D3).
        Default False preserva o comportamento absoluto.

    Portões (D3): séries com < MIN_SERIES_POINTS pontos (métrica, log, spans)
    ou métrica com efeito relativo < MIN_RELATIVE_EFFECT não geram sinal.

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

        # Sinal de métrica: agrupa anomalias POR MÉTRICA antes do slope.
        # Anomalias são deduplicadas a 1 por (service, metric_name); uma série
        # misturando métricas distintas não tem significado preditivo (F1-3).
        try:
            metric_anomalies = getattr(aggregated_report, "metric_anomalies", [])
            if metric_anomalies:
                import datetime

                from data.models import MetricPoint

                now = datetime.datetime.now()
                by_metric: dict[str, list] = {}
                for a in metric_anomalies:
                    point = MetricPoint(
                        timestamp=getattr(a, "timestamp", now) or now,
                        service=getattr(a, "service", "unknown"),
                        metric_name=getattr(a, "metric_name", "metric"),
                        value=getattr(a, "value", 0.0),
                    )
                    by_metric.setdefault(point.metric_name, []).append((a, point))

                best_slope: float | None = None
                best_eta: float | None = None
                best_sigma = 0.0
                best_rel = 0.0
                for entries in by_metric.values():
                    if len(entries) < MIN_SERIES_POINTS:
                        continue  # série curta não estabelece tendência
                    entries.sort(key=lambda e: e[1].timestamp)
                    points = [p for _, p in entries]
                    values = [p.value for p in points]
                    if _relative_effect(values) < MIN_RELATIVE_EFFECT:
                        continue  # flat/ruído: efeito ínfimo → sem sinal
                    threshold = _eta_threshold(entries[0][0], eta_sigma)
                    slope, eta = compute_ewma_slope(points, threshold=threshold)
                    # Sinal = tendência ascendente mais acentuada entre as métricas
                    if best_slope is None or slope > best_slope:
                        best_slope = slope
                        best_eta = eta
                        best_rel = relative_slope(slope, sum(values) / len(values))
                        best_sigma = max(
                            getattr(a, "deviation_sigma", 0.0) for a, _ in entries
                        )
                if best_slope is not None:
                    metric_signal = MetricTrendSignal(
                        slope=best_slope,
                        eta_minutes=best_eta,
                        change_point_detected=best_sigma > 5.0,
                        relative_slope=best_rel,
                    )
                    eta_minutes = best_eta
                    signals.append("metric_trend")
        except Exception as exc:
            logger.warning("predictive_stage.metric_signal_failed: %s", exc)

        # Sinal de log: usa log_templates para reconstruir entradas sintéticas
        try:
            log_templates = getattr(aggregated_report, "log_templates", [])
            if log_templates:
                # Constrói entradas de log sintéticas a partir dos templates
                import datetime

                from data.models import LogEntry

                now = datetime.datetime.now()
                log_entries = []
                for tpl in log_templates:
                    count = getattr(tpl, "count", 1)
                    n = min(count, 10)  # limita para evitar explosion
                    first = getattr(tpl, "first_seen", None)
                    last = getattr(tpl, "last_seen", None)
                    for i in range(n):
                        # Timestamps reais do template quando disponíveis (F1-3):
                        # distribui as entradas uniformemente em [first, last]
                        if (
                            first is not None
                            and last is not None
                            and last > first
                            and n > 1
                        ):
                            ts = first + (last - first) * (i / (n - 1))
                        else:
                            ts = last or first or now
                        log_entries.append(
                            LogEntry(
                                timestamp=ts,
                                level=getattr(tpl, "level", "INFO"),
                                service=next(
                                    iter(getattr(tpl, "services", {"unknown"})),
                                    "unknown",
                                ),
                                message=str(getattr(tpl, "template", "")),
                            )
                        )
                if len(log_entries) >= MIN_SERIES_POINTS:
                    log_signal = analyze_log_signals(log_entries)
                    signals.append("log_burst")
        except Exception as exc:
            logger.warning("predictive_stage.log_signal_failed: %s", exc)

        # Sinal de latência: usa slow_traces para construir spans sintéticos
        try:
            slow_traces = getattr(aggregated_report, "slow_traces", [])
            if slow_traces:
                import datetime
                import uuid

                from data.models import Span

                now = datetime.datetime.now()
                spans = []
                for trace in slow_traces:
                    # SlowCallPath tem total_duration_ms (não duration_ms) e
                    # path é list[str] — Span.operation é str (F1-3)
                    duration = getattr(trace, "total_duration_ms", 0.0)
                    path = getattr(trace, "path", None)
                    if isinstance(path, (list, tuple)):
                        operation = " -> ".join(str(s) for s in path) or "unknown"
                    elif isinstance(path, str):
                        operation = path
                    else:
                        operation = "unknown"
                    spans.append(
                        Span(
                            trace_id=str(uuid.uuid4()),
                            span_id=str(uuid.uuid4()),
                            parent_span_id=None,
                            service=getattr(trace, "bottleneck_service", "unknown"),
                            operation=operation,
                            start_time=getattr(trace, "timestamp", None) or now,
                            duration_ms=duration,
                            status="ok",
                        )
                    )
                if len(spans) >= MIN_SERIES_POINTS:
                    latency_signal = analyze_trace_signals(spans)
                    signals.append("latency_drift")
        except Exception as exc:
            logger.warning("predictive_stage.latency_signal_failed: %s", exc)

        # Calcula score de risco unificado
        risk_score = compute_risk_score(
            metric_signal, log_signal, latency_signal, use_relative_slope
        )
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
