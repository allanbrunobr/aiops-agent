from __future__ import annotations
"""
Analisador de demonstração: executa o pipeline preditivo sobre os dados
recebidos de um tick e retorna sinais, score de risco e PredictionReport.

DemoAnalyzer é uma camada thin sobre os módulos pipeline/* existentes,
adaptada para operar sobre snapshots incrementais do corpus.
"""

from data.models import LogEntry, MetricPoint, Span
from pipeline.log_predictor import analyze_log_signals, LogBurstSignal
from pipeline.metric_predictor import compute_ewma_slope
from pipeline.risk_scorer import compute_risk_score, get_event_level, MetricTrendSignal
from pipeline.trace_predictor import analyze_trace_signals, LatencyDriftSignal


class DemoAnalyzer:
    """Executa análise preditiva incremental sobre o buffer de eventos do tick."""

    def __init__(self, enable_prediction: bool = True, ewma_threshold: float = 0.90) -> None:
        # Se False, usa apenas desvios 3-sigma sem sinais preditivos
        self.enable_prediction = enable_prediction
        # Limiar para cálculo de ETA (ex: 7200.0 para memory_used_mb, 0.90 para métricas normalizadas)
        self.ewma_threshold = ewma_threshold
        # Contador de ticks sustentados acima do limiar de warning (escalação progressiva)
        self._warning_ticks: int = 0

    def analyze(
        self,
        log_entries: list[LogEntry],
        metric_points: list[MetricPoint],
        spans: list[Span],
    ) -> dict:
        """Analisa os dados acumulados e retorna dict com sinais e score.

        Retorna
        -------
        dict com chaves: risk_score, signals, prediction, metrics
        """
        # Agrupa métricas por nome para análise
        metrics_by_name: dict[str, list[MetricPoint]] = {}
        for mp in metric_points:
            metrics_by_name.setdefault(mp.metric_name, []).append(mp)

        # Métricas resumidas (último valor de cada nome)
        metrics_summary: dict[str, float] = {
            name: pts[-1].value
            for name, pts in metrics_by_name.items()
            if pts
        }

        if not self.enable_prediction:
            # Modo tradicional: apenas desvio 3-sigma baseado em MetricAnomaly
            risk_score = self._compute_3sigma_risk(metrics_by_name)
            return {
                "risk_score": risk_score,
                "signals": {
                    "metric_anomaly": risk_score > 30.0,
                    "log_burst_detected": False,
                    "novel_templates": [],
                    "latency_drift_detected": False,
                    "p95_ratio": 1.0,
                    "p99_ratio": 1.0,
                    "slope_ms_per_min": 0.0,
                },
                "prediction": None,
                "metrics": metrics_summary,
            }

        # === Modo preditivo completo ===

        # 1. Sinal de log burst
        log_signal: LogBurstSignal | None = None
        if log_entries:
            log_signal = analyze_log_signals(log_entries)

        # 2. Sinal de tendência de métrica (usa pool_active ou memory_mb ou cpu)
        metric_signal: MetricTrendSignal | None = None
        primary_metric = (
            metrics_by_name.get("connection_pool_active")
            or metrics_by_name.get("memory_used_mb")
            or metrics_by_name.get("cpu_usage")
        )
        if primary_metric and len(primary_metric) >= 2:
            slope, eta_minutes = compute_ewma_slope(
                primary_metric, span=10, threshold=self.ewma_threshold
            )
            metric_signal = MetricTrendSignal(
                slope=slope,
                eta_minutes=eta_minutes,
                change_point_detected=(slope > 0.5),
            )

        # 3. Sinal de drift de latência
        latency_signal: LatencyDriftSignal | None = None
        if spans and len(spans) >= 4:
            latency_signal = analyze_trace_signals(spans)

        # 4. Score de risco composto
        risk_score = compute_risk_score(metric_signal, log_signal, latency_signal)

        # Escalação progressiva para demo: quanto mais tempo o sistema fica degradado,
        # maior a confiança de incidente. A cada 15 ticks com risco >= 30, +5 pontos (máx +30).
        # 15 ticks @ 1fps = ~15s reais → warning visível ~45s antes de virar alert.
        if risk_score >= 30.0:
            self._warning_ticks += 1
            bonus = min(30.0, (self._warning_ticks // 15) * 5.0)
            risk_score = min(100.0, risk_score + bonus)
        else:
            self._warning_ticks = 0

        event_level = get_event_level(risk_score)

        # 5. Monta sinais para o TickFrame
        signals: dict = {
            "metric_anomaly": metric_signal is not None and metric_signal.slope > 0.1,
            "log_burst_detected": log_signal.burst_detected if log_signal else False,
            "novel_templates": list(log_signal.novel_templates) if log_signal else [],
            "latency_drift_detected": latency_signal.drift_detected if latency_signal else False,
            "p95_ratio": latency_signal.p95_ratio if latency_signal else 1.0,
            "p99_ratio": latency_signal.p99_ratio if latency_signal else 1.0,
            "slope_ms_per_min": (metric_signal.slope * 60.0) if metric_signal else 0.0,
        }

        # 6. PredictionReport como dict para o TickFrame
        prediction: dict | None = None
        if event_level is not None:
            prediction = {
                "event_level": event_level,
                "risk_score": risk_score,
                "eta_minutes": metric_signal.eta_minutes if metric_signal else None,
                "signals_fired": [k for k, v in signals.items() if v and k != "novel_templates"],
                "matched_history": [],
            }

        return {
            "risk_score": risk_score,
            "signals": signals,
            "prediction": prediction,
            "metrics": metrics_summary,
        }

    def _compute_3sigma_risk(self, metrics_by_name: dict[str, list[MetricPoint]]) -> float:
        """Calcula score de risco simplificado usando apenas desvio 3-sigma.

        Usado no modo enable_prediction=False (painel Traditional).
        """
        total_score = 0.0
        for name, pts in metrics_by_name.items():
            if len(pts) < 4:
                continue
            values = [p.value for p in pts]
            n = len(values)
            mean = sum(values) / n
            variance = sum((v - mean) ** 2 for v in values) / n
            stddev = variance ** 0.5
            if stddev < 1e-9:
                continue
            last_val = values[-1]
            z_score = abs(last_val - mean) / stddev
            if z_score > 3.0:
                total_score = max(total_score, min(100.0, z_score * 20.0))
        return total_score
