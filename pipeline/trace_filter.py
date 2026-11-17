"""
Filtro de traces por latência (p95) com extração de call paths.

Baseado no paper OpsAgent (Nankai University, Oct 2025):
  - Identifica spans lentos usando threshold percentil (p95)
  - Extrai call paths de 3 hops para revelar bottlenecks
  - Agrupa por janelas de 60s
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

import numpy as np

try:
    import _aiops_rust as _rust

    _RUST_AVAILABLE = True
except ImportError:
    _rust = None
    _RUST_AVAILABLE = False


_rust_params_ok: bool | None = None


def _rust_supports_params() -> bool:
    """A extensão instalada aceita ``percentile_threshold``/``max_hops``?

    Extensão compilada antes de F6-1 só aceita ``(spans)``: detecta uma vez,
    avisa e usa o Python. Recompile com ``scripts/build_rust.sh``.
    """
    global _rust_params_ok
    if _rust_params_ok is None:
        sig = getattr(_rust.filter_traces, "__text_signature__", "") or ""
        _rust_params_ok = "percentile_threshold" in sig
        if not _rust_params_ok:
            import structlog

            structlog.get_logger("pipeline.trace_filter").warning(
                "rust.extension_stale",
                function="filter_traces",
                detail=(
                    "_aiops_rust compilado antes de F6-1 não aceita parâmetros; "
                    "usando fallback Python. Recompile: scripts/build_rust.sh"
                ),
            )
    return _rust_params_ok


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
        """Identifica traces lentos e extrai call paths.

        Rust quando a extensão existe — com OS MESMOS parâmetros desta instância.
        F6-1: antes só os defaults (p95/3 hops) iam para o Rust; qualquer config
        custom caía no Python sem aviso, e os dois paths divergiam.
        """
        if _RUST_AVAILABLE and _rust_supports_params():
            return self._filter_rust(spans)
        return self._filter_python(spans)

    def _filter_rust(self, spans: list) -> list[SlowCallPath]:
        """Delegação para implementação Rust (percentil + max_hops paramétricos)."""
        raw = _rust.filter_traces(
            spans,
            percentile_threshold=self.percentile_threshold,
            max_hops=self.max_hops,
        )
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
        below = [d for d in all_durations if d < threshold]
        # F6-5: np.mean([]) → nan quando todas as durações são iguais (nada abaixo
        # do percentil). Rust usa o próprio threshold como baseline nesse caso.
        baseline_mean = float(np.mean(below)) if below else float(threshold)

        # Filtra traces acima do threshold
        slow_paths = []
        for trace_id, duration in trace_durations.items():
            if duration < threshold:
                continue

            trace_spans = sorted(traces[trace_id], key=lambda s: s.start_time)
            # F6-5: call path = DFS parent→child (ordem CAUSAL), como no Rust. Antes o
            # DFS era computado e descartado; path/operations usavam
            # trace_spans[:max_hops] por start_time (ordem temporal ≠ causal).
            path = self._extract_call_path(trace_spans)

            if path:
                # Encontra bottleneck (span mais lento)
                bottleneck = max(trace_spans, key=lambda s: s.duration_ms)
                slow_paths.append(
                    SlowCallPath(
                        trace_id=trace_id,
                        path=[s.service for s in path],
                        operations=[s.operation for s in path],
                        total_duration_ms=duration,
                        bottleneck_service=bottleneck.service,
                        bottleneck_duration_ms=bottleneck.duration_ms,
                        normal_duration_ms=round(baseline_mean, 1),
                        timestamp=trace_spans[0].start_time,
                        attributes=bottleneck.attributes,
                    )
                )

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
            return spans[: self.max_hops]

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
            attrs = (
                ", ".join(f"{k}={v}" for k, v in p.attributes.items())
                if p.attributes
                else "none"
            )
            lines.append(
                f"- {path_str}: {p.total_duration_ms:.0f}ms "
                f"(normal: {p.normal_duration_ms:.0f}ms) "
                f"| bottleneck: {p.bottleneck_service} ({p.bottleneck_duration_ms:.0f}ms) "
                f"| attrs: {attrs}"
            )
        return "\n".join(lines)
