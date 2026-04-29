"""
Detection Agent — Analisa dados agregados e decide se há incidente.

Baseado no paper STRATUS: primeiro agente no pipeline,
recebe dados pré-processados e classifica severidade.
"""

from __future__ import annotations

from application.ports.reporter import INCIDENT_DETECTED, NullReporter, Reporter


class DetectionAgent:
    """Agente de detecção de incidentes."""

    def __init__(
        self,
        error_threshold: int = 50,
        sigma_threshold: float = 3.0,
        reporter: Reporter | None = None,
    ):
        self.error_threshold = error_threshold
        self.sigma_threshold = sigma_threshold
        self.reporter = reporter or NullReporter()

    def detect(self, report) -> dict:
        """Analisa o relatório agregado e decide se há incidente."""
        # Conta errors
        error_templates = [t for t in report.log_templates if t.level in ("ERROR", "FATAL")]
        total_errors = sum(t.count for t in error_templates)

        # Verifica anomalias de métricas
        high_sigma_anomalies = [a for a in report.metric_anomalies if a.deviation_sigma >= self.sigma_threshold]

        # Verifica traces lentos
        slow_traces = report.slow_traces

        # Heurística de detecção
        signals = []
        if total_errors >= self.error_threshold:
            signals.append(f"{total_errors} errors across {len(error_templates)} patterns")
        if high_sigma_anomalies:
            signals.append(f"{len(high_sigma_anomalies)} metric anomalies (>{self.sigma_threshold}sigma)")
        if slow_traces:
            signals.append(f"{len(slow_traces)} slow trace patterns")

        is_incident = len(signals) >= 2  # ≥2 sinais correlacionados

        self.reporter.emit(
            INCIDENT_DETECTED,
            total_logs=report.total_logs,
            template_count=len(report.log_templates),
            anomaly_count=len(report.metric_anomalies),
            trace_count=len(report.slow_traces),
            is_incident=is_incident,
            signals=signals,
        )

        return {
            "is_incident": is_incident,
            "severity": report.severity,
            "affected_services": list(report.affected_services),
            "signals": signals,
            "total_errors": total_errors,
            "anomaly_count": len(high_sigma_anomalies),
            "slow_trace_count": len(slow_traces),
        }
