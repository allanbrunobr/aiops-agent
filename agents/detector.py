"""
Detection Agent — Analisa dados agregados e decide se há incidente.

Baseado no paper STRATUS: primeiro agente no pipeline,
recebe dados pré-processados e classifica severidade.
"""

from __future__ import annotations

from rich.console import Console
from rich.tree import Tree

console = Console()


class DetectionAgent:
    """Agente de detecção de incidentes."""

    def __init__(self, error_threshold: int = 50, sigma_threshold: float = 3.0):
        self.error_threshold = error_threshold
        self.sigma_threshold = sigma_threshold

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

        # Output visual
        tree = Tree(f"[bold]Detection Analysis[/bold]")
        tree.add(f"Logs: {report.total_logs} processed → {len(report.log_templates)} templates (Drain3)")
        tree.add(f"Metrics: {len(report.metric_anomalies)} anomalies detected (3-sigma)")
        tree.add(f"Traces: {len(report.slow_traces)} slow patterns identified (>p95)")

        if is_incident:
            tree.add(f"[bold red]INCIDENT DETECTED[/bold red]: {len(signals)} correlated signals")
        else:
            tree.add(f"[green]No incident[/green]: insufficient correlated signals")

        console.print(tree)

        return {
            "is_incident": is_incident,
            "severity": report.severity,
            "affected_services": list(report.affected_services),
            "signals": signals,
            "total_errors": total_errors,
            "anomaly_count": len(high_sigma_anomalies),
            "slow_trace_count": len(slow_traces),
        }
