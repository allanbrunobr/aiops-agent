"""
Filtro de métricas por anomalia usando 3-sigma.

Baseado no paper OpsAgent (Nankai University, Oct 2025):
  - 3-sigma statistical filtering para detectar anomalias
  - Classificação de forma (spike, level_shift, steady_increase)
  - Conversão para formato textual para o LLM
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from datetime import datetime
from collections import defaultdict

try:
    import _aiops_rust as _rust
    _RUST_AVAILABLE = True
except ImportError:
    _rust = None
    _RUST_AVAILABLE = False


@dataclass
class MetricAnomaly:
    """Uma anomalia detectada em uma métrica."""
    service: str
    metric_name: str
    anomaly_type: str  # "spike", "level_shift", "steady_increase"
    timestamp: datetime
    value: float
    mean: float
    std: float
    deviation_sigma: float
    description: str


class MetricFilter:
    """Detecta anomalias em séries temporais usando 3-sigma."""

    def __init__(self, sigma_threshold: float = 3.0, baseline_ratio: float = 0.5):
        self.sigma_threshold = sigma_threshold
        self.baseline_ratio = baseline_ratio  # % dos dados usados como baseline

    def detect_anomalies(self, metrics: list) -> list[MetricAnomaly]:
        """Detecta anomalias em todas as métricas."""
        if _RUST_AVAILABLE and self.sigma_threshold == 3.0 and self.baseline_ratio == 0.5:
            return self._detect_rust(metrics)
        return self._detect_python(metrics)

    def _detect_rust(self, metrics: list) -> list[MetricAnomaly]:
        """Delegação para implementação Rust (3-sigma com defaults)."""
        raw = _rust.filter_metrics(metrics)
        result = []
        for r in raw:
            ts = datetime.fromtimestamp(r["timestamp"])
            result.append(MetricAnomaly(
                service=r["service"],
                metric_name=r["metric_name"],
                anomaly_type=r["anomaly_type"],
                timestamp=ts,
                value=r["value"],
                mean=r["mean"],
                std=r["std"],
                deviation_sigma=r["deviation_sigma"],
                description=self._describe(
                    r["service"], r["metric_name"], r["anomaly_type"],
                    ts, r["value"], r["mean"], r["deviation_sigma"],
                ),
            ))
        return result

    def _detect_python(self, metrics: list) -> list[MetricAnomaly]:
        """Implementação Python pura (fallback)."""
        # Agrupa por (service, metric_name)
        grouped: dict[tuple[str, str], list] = defaultdict(list)
        for m in metrics:
            grouped[(m.service, m.metric_name)].append(m)

        anomalies = []
        for (service, metric_name), points in grouped.items():
            points.sort(key=lambda p: p.timestamp)
            values = np.array([p.value for p in points])
            timestamps = [p.timestamp for p in points]

            # Usa primeira metade como baseline (pre-incidente)
            baseline_end = int(len(values) * self.baseline_ratio)
            baseline = values[:baseline_end]

            if len(baseline) < 5:
                continue

            mean = float(np.mean(baseline))
            std = float(np.std(baseline))

            if std < 1e-6:
                std = 0.01  # evita divisão por zero

            # Detecta pontos anômalos na segunda metade
            for i in range(baseline_end, len(values)):
                deviation = abs(values[i] - mean) / std
                if deviation >= self.sigma_threshold:
                    anomaly_type = self._classify_shape(values, i, baseline_end)
                    anomalies.append(MetricAnomaly(
                        service=service,
                        metric_name=metric_name,
                        anomaly_type=anomaly_type,
                        timestamp=timestamps[i],
                        value=round(values[i], 2),
                        mean=round(mean, 2),
                        std=round(std, 2),
                        deviation_sigma=round(deviation, 1),
                        description=self._describe(
                            service, metric_name, anomaly_type,
                            timestamps[i], values[i], mean, deviation
                        ),
                    ))

        # Deduplica: mantém apenas o ponto de maior desvio por (service, metric)
        return self._deduplicate(anomalies)

    def _classify_shape(self, values: np.ndarray, idx: int, baseline_end: int) -> str:
        """Classifica a forma da anomalia (simplificado, sem CNN do OpsAgent)."""
        window = values[max(baseline_end, idx - 10):idx + 1]
        if len(window) < 3:
            return "spike"

        # Gradiente
        diffs = np.diff(window)
        if np.all(diffs > 0):
            return "steady_increase"
        elif abs(window[-1] - window[-2]) > 2 * np.std(window[:-1]):
            return "spike"
        else:
            return "level_shift"

    def _describe(self, service: str, metric: str, anomaly_type: str,
                  ts: datetime, value: float, mean: float, deviation: float) -> str:
        """Gera descrição textual da anomalia (formato OpsAgent)."""
        ts_str = ts.strftime("%H:%M:%S")
        return (
            f"<{service}, {metric}, {anomaly_type}: {ts_str}, "
            f"value={value:.1f}, baseline_mean={mean:.1f}, deviation={deviation:.1f}sigma>"
        )

    def _deduplicate(self, anomalies: list[MetricAnomaly]) -> list[MetricAnomaly]:
        """Mantém apenas a anomalia de maior desvio por (service, metric)."""
        best: dict[tuple[str, str], MetricAnomaly] = {}
        for a in anomalies:
            key = (a.service, a.metric_name)
            if key not in best or a.deviation_sigma > best[key].deviation_sigma:
                best[key] = a
        return sorted(best.values(), key=lambda a: a.deviation_sigma, reverse=True)

    def to_text(self, anomalies: list[MetricAnomaly]) -> str:
        """Converte anomalias em texto estruturado para o LLM."""
        if not anomalies:
            return "## Métricas\nNenhuma anomalia detectada."

        lines = ["## Métricas Anômalas (3-sigma filter)\n"]
        for a in anomalies:
            lines.append(
                f"- {a.service}/{a.metric_name}: {a.anomaly_type} "
                f"| value={a.value} (baseline={a.mean}) "
                f"| {a.deviation_sigma}sigma "
                f"| {a.timestamp.strftime('%H:%M:%S')}"
            )
        return "\n".join(lines)
