"""
Filtro de traces por latência (p95) com extração de call paths.

Baseado no paper OpsAgent (Nankai University, Oct 2025):
  - Identifica spans lentos usando threshold percentil (p95)
  - Extrai call paths de 3 hops para revelar bottlenecks
  - Agrupa por janelas de 60s
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from datetime import datetime, timedelta
from collections import defaultdict

try:
    import _aiops_rust as _rust
    _RUST_AVAILABLE = True
except ImportError:
    _rust = None
    _RUST_AVAILABLE = False


@dataclass
class SlowCallPath:
    """Um call path lento extraído dos traces."""
    trace_id: str
    path: list[str]  # ["gateway", "payment-svc", "postgres"]
    operations: list[str]
    total_duration_ms: float
    bottleneck_service: str
    bottleneck_duration_ms: float
    normal_duration_ms: float  # média do baseline para comparação
    timestamp: datetime
    attributes: dict


class TraceFilter:
    """Filtra traces por latência e extrai call paths problemáticos."""

    def __init__(self, percentile_threshold: float = 95.0, max_hops: int = 3):
        self.percentile_threshold = percentile_threshold
        self.max_hops = max_hops

    def filter_slow_traces(self, spans: list) -> list[SlowCallPath]:
        """Identifica traces lentos e extrai call paths."""
        if _RUST_AVAILABLE and self.percentile_threshold == 95.0 and self.max_hops == 3:
            return self._filter_rust(spans)
        return self._filter_python(spans)

    def _filter_rust(self, spans: list) -> list[SlowCallPath]:
        """Delegação para implementação Rust (p95 + max_hops=3)."""
        raw = _rust.filter_traces(spans)
        return [
            SlowCallPath(
                trace_id=r["trace_id"],
                path=r["path"],
                operations=r["operations"],
                total_duration_ms=r["total_duration_ms"],
                bottleneck_service=r["bottleneck_service"],
                bottleneck_duration_ms=r["bottleneck_duration_ms"],
                normal_duration_ms=r["normal_duration_ms"],
                timestamp=r["timestamp"],
                attributes=dict(r["attributes"]),
            )
            for r in raw
        ]

    def _filter_python(self, spans: list) -> list[SlowCallPath]:
        """Implementação Python pura (fallback)."""
        # Agrupa spans por trace_id
        traces: dict[str, list] = defaultdict(list)
        for span in spans:
            traces[span.trace_id].append(span)

        # Calcula durações por trace (root span)
        trace_durations = {}
        for trace_id, trace_spans in traces.items():
            root = [s for s in trace_spans if s.parent_span_id is None]
            if root:
                trace_durations[trace_id] = root[0].duration_ms

        if not trace_durations:
            return []

        # Calcula p95 threshold
        all_durations = list(trace_durations.values())
        threshold = float(np.percentile(all_durations, self.percentile_threshold))
        baseline_mean = float(np.mean([d for d in all_durations if d < threshold]))

        # Filtra traces acima do threshold
        slow_paths = []
        for trace_id, duration in trace_durations.items():
            if duration < threshold:
                continue

            trace_spans = sorted(traces[trace_id], key=lambda s: s.start_time)
            path = self._extract_call_path(trace_spans)

            if path:
                # Encontra bottleneck (span mais lento)
                bottleneck = max(trace_spans, key=lambda s: s.duration_ms)
                slow_paths.append(SlowCallPath(
                    trace_id=trace_id,
                    path=[s.service for s in trace_spans[:self.max_hops]],
                    operations=[s.operation for s in trace_spans[:self.max_hops]],
                    total_duration_ms=duration,
                    bottleneck_service=bottleneck.service,
                    bottleneck_duration_ms=bottleneck.duration_ms,
                    normal_duration_ms=round(baseline_mean, 1),
                    timestamp=trace_spans[0].start_time,
                    attributes=bottleneck.attributes,
                ))

        # Deduplica por padrão de path (mantém o mais lento de cada padrão)
        return self._deduplicate_paths(slow_paths)

    def _extract_call_path(self, spans: list) -> list:
        """Extrai call path ordenado do trace."""
        if not spans:
            return []

        # Build parent-child tree
        children: dict[str | None, list] = defaultdict(list)
        for s in spans:
            children[s.parent_span_id].append(s)

        # DFS from root
        roots = children.get(None, [])
        if not roots:
            return spans[:self.max_hops]

        path = []
        current = [roots[0]]
        while current and len(path) < self.max_hops:
            span = current[0]
            path.append(span)
            current = children.get(span.span_id, [])

        return path

    def _deduplicate_paths(self, paths: list[SlowCallPath]) -> list[SlowCallPath]:
        """Mantém o trace mais lento de cada padrão de path."""
        best: dict[str, SlowCallPath] = {}
        for p in paths:
            key = " → ".join(p.path)
            if key not in best or p.total_duration_ms > best[key].total_duration_ms:
                best[key] = p
        return sorted(best.values(), key=lambda p: p.total_duration_ms, reverse=True)

    def to_text(self, slow_paths: list[SlowCallPath]) -> str:
        """Converte traces lentos em texto para o LLM."""
        if not slow_paths:
            return "## Traces\nNenhum trace lento detectado."

        lines = [f"## Traces Lentos (>{self.percentile_threshold}th percentile)\n"]
        for p in slow_paths:
            path_str = " → ".join(p.path)
            attrs = ", ".join(f"{k}={v}" for k, v in p.attributes.items()) if p.attributes else "none"
            lines.append(
                f"- {path_str}: {p.total_duration_ms:.0f}ms "
                f"(normal: {p.normal_duration_ms:.0f}ms) "
                f"| bottleneck: {p.bottleneck_service} ({p.bottleneck_duration_ms:.0f}ms) "
                f"| attrs: {attrs}"
            )
        return "\n".join(lines)
