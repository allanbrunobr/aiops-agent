"""
Agregador que combina outputs do pipeline em texto estruturado para o LLM.

Converte dados pré-processados (templates, anomalias, traces lentos)
em um relatório textual unificado, pronto para ingestão pelo agente.
"""

from __future__ import annotations

from datetime import datetime
from pipeline.log_parser import LogParser, LogTemplate
from pipeline.metric_filter import MetricFilter, MetricAnomaly
from pipeline.trace_filter import TraceFilter, SlowCallPath


class Aggregator:
    """Combina dados de observabilidade em texto para o LLM."""

    def __init__(self, sigma_threshold: float = 3.0, baseline_ratio: float = 0.5,
                 percentile_threshold: float = 95.0, max_hops: int = 3):
        self.log_parser = LogParser()
        self.metric_filter = MetricFilter(
            sigma_threshold=sigma_threshold,
            baseline_ratio=baseline_ratio,
        )
        self.trace_filter = TraceFilter(
            percentile_threshold=percentile_threshold,
            max_hops=max_hops,
        )

    def process(self, logs: list, metrics: list, traces: list) -> AggregatedReport:
        """Processa todas as fontes e gera relatório agregado."""
        log_templates = self.log_parser.parse(logs)
        metric_anomalies = self.metric_filter.detect_anomalies(metrics)
        slow_traces = self.trace_filter.filter_slow_traces(traces)

        return AggregatedReport(
            log_templates=log_templates,
            metric_anomalies=metric_anomalies,
            slow_traces=slow_traces,
            total_logs=len(logs),
            total_metrics=len(metrics),
            total_traces=len(traces),
        )

    def process_with_templates(
        self,
        log_templates: list[LogTemplate],
        metrics: list,
        traces: list,
        total_logs: int,
    ) -> AggregatedReport:
        """Gera relatório a partir de templates já parseados (evita re-parse)."""
        metric_anomalies = self.metric_filter.detect_anomalies(metrics)
        slow_traces = self.trace_filter.filter_slow_traces(traces)

        return AggregatedReport(
            log_templates=log_templates,
            metric_anomalies=metric_anomalies,
            slow_traces=slow_traces,
            total_logs=total_logs,
            total_metrics=len(metrics),
            total_traces=len(traces),
        )


class AggregatedReport:
    """Relatório agregado com dados de observabilidade processados."""

    def __init__(
        self,
        log_templates: list[LogTemplate],
        metric_anomalies: list[MetricAnomaly],
        slow_traces: list[SlowCallPath],
        total_logs: int,
        total_metrics: int,
        total_traces: int,
    ):
        self.log_templates = log_templates
        self.metric_anomalies = metric_anomalies
        self.slow_traces = slow_traces
        self.total_logs = total_logs
        self.total_metrics = total_metrics
        self.total_traces = total_traces

    @property
    def affected_services(self) -> set[str]:
        """Serviços afetados (aparecendo em anomalias)."""
        services = set()
        for tpl in self.log_templates:
            if tpl.level in ("ERROR", "FATAL"):
                services.update(tpl.services)
        for a in self.metric_anomalies:
            services.add(a.service)
        for t in self.slow_traces:
            services.add(t.bottleneck_service)
        return services

    @property
    def severity(self) -> str:
        """Severidade estimada do incidente."""
        error_count = sum(t.count for t in self.log_templates if t.level in ("ERROR", "FATAL"))
        high_sigma = any(a.deviation_sigma > 5 for a in self.metric_anomalies)

        if error_count > 1000 or high_sigma:
            return "CRITICAL"
        elif error_count > 100:
            return "HIGH"
        elif error_count > 10:
            return "MEDIUM"
        return "LOW"

    def to_text(self) -> str:
        """Gera relatório textual completo para o LLM."""
        sections = [
            self._header(),
            self._logs_section(),
            self._metrics_section(),
            self._traces_section(),
            self._summary_section(),
        ]
        return "\n\n".join(sections)

    def to_compact_text(self) -> str:
        """Versão compacta para context windows menores."""
        lines = [f"# Incident Report | Severity: {self.severity}\n"]
        lines.append(f"Affected: {', '.join(self.affected_services)}\n")

        # Top 5 log templates
        lines.append("## Top Error Logs")
        for tpl in self.log_templates[:5]:
            if tpl.level in ("ERROR", "FATAL", "WARN"):
                svcs = ", ".join(sorted(tpl.services))
                lines.append(f"- [{tpl.level}][{svcs}] \"{tpl.template}\" x{tpl.count}")

        # Anomalias
        lines.append("\n## Metric Anomalies")
        for a in self.metric_anomalies[:5]:
            lines.append(f"- {a.service}/{a.metric_name}: {a.value} ({a.deviation_sigma}sigma)")

        # Traces
        lines.append("\n## Slow Traces")
        for t in self.slow_traces[:3]:
            path = " → ".join(t.path)
            lines.append(f"- {path}: {t.total_duration_ms:.0f}ms (normal: {t.normal_duration_ms:.0f}ms)")

        return "\n".join(lines)

    def _header(self) -> str:
        return (
            f"# Observability Report\n"
            f"- Severity: {self.severity}\n"
            f"- Affected Services: {', '.join(sorted(self.affected_services))}\n"
            f"- Data processed: {self.total_logs} logs → {len(self.log_templates)} templates, "
            f"{self.total_metrics} metric points → {len(self.metric_anomalies)} anomalies, "
            f"{self.total_traces} spans → {len(self.slow_traces)} slow paths"
        )

    def _logs_section(self) -> str:
        lines = ["## Log Analysis\n"]
        for tpl in self.log_templates[:15]:
            svcs = ", ".join(sorted(tpl.services))
            first = tpl.first_seen.strftime("%H:%M:%S") if tpl.first_seen else "?"
            last = tpl.last_seen.strftime("%H:%M:%S") if tpl.last_seen else "?"
            lines.append(
                f"- [{tpl.level}][{svcs}] \"{tpl.template}\" "
                f"| count={tpl.count} | {first}→{last} | score={tpl.relevance_score}"
            )
        return "\n".join(lines)

    def _metrics_section(self) -> str:
        lines = ["## Metric Anomalies\n"]
        for a in self.metric_anomalies:
            lines.append(
                f"- {a.service}/{a.metric_name}: {a.anomaly_type} "
                f"| {a.value} (baseline: {a.mean}) | {a.deviation_sigma}sigma "
                f"| {a.timestamp.strftime('%H:%M:%S')}"
            )
        return "\n".join(lines)

    def _traces_section(self) -> str:
        lines = ["## Slow Traces\n"]
        for p in self.slow_traces:
            path_str = " → ".join(p.path)
            lines.append(
                f"- {path_str}: {p.total_duration_ms:.0f}ms "
                f"(normal: {p.normal_duration_ms:.0f}ms) "
                f"| bottleneck: {p.bottleneck_service}"
            )
            if p.attributes:
                attrs = ", ".join(f"{k}={v}" for k, v in p.attributes.items())
                lines.append(f"  attributes: {attrs}")
        return "\n".join(lines)

    def _summary_section(self) -> str:
        error_templates = [t for t in self.log_templates if t.level in ("ERROR", "FATAL")]
        total_errors = sum(t.count for t in error_templates)
        return (
            f"## Summary\n"
            f"- Total errors: {total_errors} across {len(error_templates)} patterns\n"
            f"- Metric anomalies: {len(self.metric_anomalies)} detected\n"
            f"- Slow traces: {len(self.slow_traces)} unique paths\n"
            f"- Primary suspect: {self._primary_suspect()}"
        )

    def _primary_suspect(self) -> str:
        """Heurística simples para identificar o serviço mais suspeito."""
        service_scores: dict[str, float] = {}
        for tpl in self.log_templates:
            if tpl.level in ("ERROR", "FATAL"):
                for svc in tpl.services:
                    service_scores[svc] = service_scores.get(svc, 0) + tpl.count * tpl.relevance_score
        for a in self.metric_anomalies:
            service_scores[a.service] = service_scores.get(a.service, 0) + a.deviation_sigma * 100

        if not service_scores:
            return "unknown"
        return max(service_scores, key=service_scores.get)
