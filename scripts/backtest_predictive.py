from __future__ import annotations

"""
Harness de backtest do estágio preditivo (D5).

Gera séries sintéticas com falhas injetadas (pool exhaustion, saturação de HPA,
vazamento de memória) e séries sem falha (flat, ruído puro), replaya
``run_predictive_stage`` JANELA a JANELA (batch: cada janela é um relatório
agregado independente, como o pipeline roda por incidente) e compara o
``event_level`` (``prediction.alert``/``prediction.warning``) com o ground-truth
da injeção.

Métricas:
  - recall: fração das falhas fortes com alerta ANTES do instante da falha;
  - precision: TP / (TP + FP), FP = série sem falha com qualquer alerta;
  - lead-time: minutos entre o 1º alerta e a falha (só nas detectadas);
  - flat/ruído: nº de alertas (deve ser zero).

Escopo: BATCH. O backtest replaya séries; não existe monitor contínuo (o
subsistema preditivo é invocado por incidente). Ver docs/preditivo-backtest.md.

Nada de infra real: tudo simulado, stdlib + pipeline do repo. Não grava em
``output/`` — o golden só é escrito com ``--write-golden`` (ou ``--output``).

Uso:
    python scripts/backtest_predictive.py            # imprime o relatório
    python scripts/backtest_predictive.py --write-golden
"""

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from pipeline.metric_filter import MetricAnomaly  # noqa: E402
from pipeline.predictive_stage import run_predictive_stage  # noqa: E402
from pipeline.trace_filter import SlowCallPath  # noqa: E402

GOLDEN_PATH = _REPO_ROOT / "validation" / "golden" / "predictive_backtest.json"

SERIES_LEN = 60  # amostras de 1 min
SAMPLE_MINUTES = 1.0
WINDOW = 20  # amostras por janela replayada
FIRST_WINDOW_END = 9  # 1ª janela termina aqui (10 pontos)
BASELINE_N = 15
T0 = datetime(2026, 1, 1, 12, 0, 0)

STRONG = ("pool_exhaustion", "hpa_saturation", "memory_leak")
NEGATIVE = ("flat", "noise", "noise_heavy")
KINDS = STRONG + NEGATIVE


@dataclass
class Series:
    kind: str
    seed: int
    metric_name: str
    values: list[float]
    latencies: list[float]  # latência por amostra (ms) → slow_traces
    failure_index: int | None  # None = sem falha
    ceiling: float | None = None
    meta: dict = field(default_factory=dict)


def generate_series(kind: str, seed: int) -> Series:
    """Série sintética determinística para (kind, seed)."""
    rng = random.Random(f"{kind}:{seed}")
    n = SERIES_LEN
    if kind == "pool_exhaustion":
        # Padrão de data/sample_incident.py: pool sobe de ~30 até 50 (max) e
        # satura; latência acompanha. Falha quando o pool chega ao teto.
        start, fail = 20, 45
        vals = [
            30 + rng.gauss(0, 1.0)
            if i < start
            else min(50.0, 30 + (i - start) * 20 / (fail - start)) + rng.gauss(0, 0.3)
            for i in range(n)
        ]
        vals = [min(50.0, v) for v in vals]
        lat = [120 + rng.gauss(0, 10) + max(0, i - start) * 40 for i in range(n)]
        return Series(kind, seed, "conn_pool_active", vals, lat, fail, 50.0)
    if kind == "hpa_saturation":
        # Réplicas sobem até o maxReplicas e ficam no teto; latência sobe.
        ceiling, fail = 10.0, 40
        vals = [min(ceiling, 3 + i * 0.3) + rng.gauss(0, 0.02) for i in range(n)]
        vals = [min(ceiling, v) for v in vals]
        lat = [150 + rng.gauss(0, 10) + max(0, i - 20) * 35 for i in range(n)]
        return Series(kind, seed, "hpa_replicas", vals, lat, fail, ceiling)
    if kind == "memory_leak":
        start, fail = 15, 50
        vals = [55 + rng.gauss(0, 0.5) + max(0, i - start) * 0.9 for i in range(n)]
        lat = [140 + rng.gauss(0, 10) + max(0, i - start) * 25 for i in range(n)]
        return Series(kind, seed, "memory_usage_percent", vals, lat, fail, None)
    if kind == "flat":
        vals = [40.0 for _ in range(n)]
        lat = [120.0 for _ in range(n)]
        return Series(kind, seed, "cpu_usage_percent", vals, lat, None, None)
    if kind == "noise":
        vals = [40 + rng.gauss(0, 0.4) for _ in range(n)]
        lat = [120 + rng.gauss(0, 4) for _ in range(n)]
        return Series(kind, seed, "cpu_usage_percent", vals, lat, None, None)
    if kind == "noise_heavy":
        # Ruído forte (σ=5% da média): passa o portão de efeito do D3, então
        # só o slope/EWMA separa de tendência real — sonda o limite do detector.
        vals = [40 + rng.gauss(0, 2.0) for _ in range(n)]
        lat = [120 + rng.gauss(0, 15) for _ in range(n)]
        return Series(kind, seed, "cpu_usage_percent", vals, lat, None, None)
    raise ValueError(f"kind desconhecido: {kind}")


def _baseline(values: list[float]) -> tuple[float, float]:
    base = values[:BASELINE_N]
    mean = sum(base) / len(base)
    var = sum((v - mean) ** 2 for v in base) / len(base)
    return mean, max(var**0.5, 1e-6)


def build_window_report(series: Series, end: int) -> SimpleNamespace:
    """AggregatedReport mínimo (duck-typed) com a janela [end-WINDOW+1, end]."""
    lo = max(0, end - WINDOW + 1)
    mean, std = _baseline(series.values)
    anomalies = [
        MetricAnomaly(
            service="svc",
            metric_name=series.metric_name,
            anomaly_type="steady_increase",
            timestamp=T0 + timedelta(minutes=i * SAMPLE_MINUTES),
            value=series.values[i],
            mean=mean,
            std=std,
            deviation_sigma=abs(series.values[i] - mean) / std,
            description="backtest",
        )
        for i in range(lo, end + 1)
    ]
    traces = [
        SlowCallPath(
            trace_id=f"t{i}",
            path=["gateway", "svc"],
            operations=["op"],
            total_duration_ms=series.latencies[i],
            bottleneck_service="svc",
            bottleneck_duration_ms=series.latencies[i],
            normal_duration_ms=120.0,
            timestamp=T0 + timedelta(minutes=i * SAMPLE_MINUTES),
            attributes={},
        )
        for i in range(lo, end + 1)
    ]
    return SimpleNamespace(
        metric_anomalies=anomalies, slow_traces=traces, log_templates=[]
    )


def replay_series(series: Series) -> dict:
    """Replaya as janelas de uma série; devolve níveis por janela."""
    ceilings = {series.metric_name: series.ceiling} if series.ceiling else None
    levels: list[tuple[int, str | None]] = []
    for end in range(FIRST_WINDOW_END, SERIES_LEN):
        report = run_predictive_stage(
            build_window_report(series, end), ceilings=ceilings
        )
        levels.append((end, report.event_level if report else None))

    # Para séries com falha só contam alertas ANTES da falha; depois dela o
    # alerta é consequência, não predição.
    limit = series.failure_index if series.failure_index is not None else SERIES_LEN
    pre = [(i, lv) for i, lv in levels if i < limit and lv is not None]
    first_alert = pre[0][0] if pre else None
    lead = (
        (series.failure_index - first_alert) * SAMPLE_MINUTES
        if first_alert is not None and series.failure_index is not None
        else None
    )
    return {
        "kind": series.kind,
        "seed": series.seed,
        "failure_index": series.failure_index,
        "first_alert_index": first_alert,
        "lead_time_min": lead,
        "alert_windows": len(pre),
    }


def run_backtest(seeds: int = 5, base_seed: int = 42) -> dict:
    """Executa o backtest e devolve o relatório agregado (JSON-serializável)."""
    rows = [
        replay_series(generate_series(kind, base_seed + s))
        for kind in KINDS
        for s in range(seeds)
    ]
    strong = [r for r in rows if r["kind"] in STRONG]
    neg = [r for r in rows if r["kind"] in NEGATIVE]
    tp = sum(1 for r in strong if r["first_alert_index"] is not None)
    fn = len(strong) - tp
    fp = sum(1 for r in neg if r["alert_windows"] > 0)
    leads = [r["lead_time_min"] for r in strong if r["lead_time_min"] is not None]
    per_kind = {
        k: {
            "series": sum(1 for r in rows if r["kind"] == k),
            "detected": sum(
                1 for r in rows if r["kind"] == k and r["first_alert_index"] is not None
            ),
        }
        for k in KINDS
    }
    return {
        "subsystem": "predictive_backtest",
        "scope": "batch (janelas replayadas; sem monitor contínuo)",
        "config": {
            "seeds": seeds,
            "base_seed": base_seed,
            "series_len": SERIES_LEN,
            "window": WINDOW,
            "sample_minutes": SAMPLE_MINUTES,
        },
        "metrics": {
            "recall": tp / len(strong) if strong else 0.0,
            "precision": tp / (tp + fp) if (tp + fp) else 1.0,
            "mean_lead_time_min": sum(leads) / len(leads) if leads else 0.0,
            "min_lead_time_min": min(leads) if leads else 0.0,
            "true_positives": tp,
            "false_negatives": fn,
            "false_positives": fp,
            "negative_alert_windows": sum(r["alert_windows"] for r in neg),
        },
        "per_kind": per_kind,
        "series": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--base-seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--write-golden", action="store_true")
    args = parser.parse_args(argv)

    report = run_backtest(args.seeds, args.base_seed)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    target = GOLDEN_PATH if args.write_golden else args.output
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    m = report["metrics"]
    print(
        f"recall={m['recall']:.2f} precision={m['precision']:.2f} "
        f"lead_medio={m['mean_lead_time_min']:.1f}min "
        f"alertas_negativos={m['negative_alert_windows']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
