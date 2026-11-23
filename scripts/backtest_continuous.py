from __future__ import annotations

"""
Backtest do monitor contínuo (VB4).

Replaya TICK A TICK, via ``CsvMetricsSource`` + ``ContinuousMonitor`` (camada
barata, sem LLM), duas famílias de séries:

  - cenário HPA fictício (``data/hpa_saturation_scenario``, VB-T1), várias
    seeds; rótulo = ``MAXED_OUT_AT``;
  - séries sintéticas do D5 (pool_exhaustion, memory_leak, hpa_saturation
    positivas; flat/noise negativas; noise_heavy só reportado), rótulo =
    ``failure_index``.

Métricas:
  - lead time: minutos entre o 1º aviso (``tick.level`` não nulo, ANTES da
    falha) e o instante da falha;
  - recall: positivos com aviso antes da falha / positivos;
  - precision: TP / (TP + FP), FP = série sem falha com qualquer aviso;
  - falsos positivos em flat/ruído puro (deve ser zero).

Nada de infra real; CSVs vão para um diretório temporário. O golden só é
escrito com ``--write-golden`` (ou ``--output``); nunca em ``output/``.

Uso:
    python scripts/backtest_continuous.py
    python scripts/backtest_continuous.py --write-golden
"""

import argparse
import csv
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import data.hpa_saturation_scenario as hpa  # noqa: E402
from pipeline.continuous_monitor import ContinuousMonitor, MonitorTick  # noqa: E402
from tools.csv_metrics_source import CsvMetricsSource  # noqa: E402

# Reuso do gerador de séries do D5 (mesmo padrão de import dos testes).
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import backtest_predictive as bp  # noqa: E402

GOLDEN_PATH = _REPO_ROOT / "validation" / "golden" / "continuous_backtest.json"

HPA_ESCALATE_ETA_MIN = 60.0
SYNTH_ESCALATE_ETA_MIN = 30.0
SYNTH_FIRST_TICK = 10  # amostras de aquecimento antes de começar a medir
POSITIVE_SYNTH = bp.STRONG
NEGATIVE_SYNTH = ("flat", "noise")  # noise_heavy: reportado, fora da asserção
EXTRA_SYNTH = ("noise_heavy",)


def _replay(
    mon: ContinuousMonitor, start: datetime, end: datetime
) -> list[MonitorTick]:
    out: list[MonitorTick] = []
    t = start
    while t <= end:
        out.append(mon.tick(t))
        t += timedelta(minutes=1)
    return out


def _summarize(
    kind: str,
    seed: int,
    ticks: list[MonitorTick],
    failure_at: datetime | None,
    escalate_ticks_before: int,
) -> dict:
    limit = (
        failure_at
        if failure_at is not None
        else ticks[-1].timestamp + timedelta(minutes=1)
    )
    pre = [t for t in ticks if t.timestamp < limit and t.level is not None]
    first = pre[0].timestamp if pre else None
    lead = (
        (failure_at - first).total_seconds() / 60
        if first is not None and failure_at is not None
        else None
    )
    return {
        "kind": kind,
        "seed": seed,
        "ticks": len(ticks),
        "first_alert_offset_min": (
            (first - ticks[0].timestamp).total_seconds() / 60 if first else None
        ),
        "lead_time_min": lead,
        "alert_ticks": len(pre),
        "escalate_ticks": escalate_ticks_before,
        "positive": failure_at is not None,
    }


def replay_hpa(seed: int, workdir: Path) -> dict:
    csv_path = hpa.export_csv(workdir / f"hpa_{seed}.csv", seed=seed)
    mon = ContinuousMonitor(
        CsvMetricsSource(csv_path),
        metrics=[hpa.METRIC_MEMORY],
        ewma_threshold=0.9 * hpa.MEMORY_REQUEST_BYTES,
        escalate_eta_minutes=HPA_ESCALATE_ETA_MIN,
        # Utilização crônica acima do alvo do HPA (80% do request) = saturação
        # iminente (o platô real é em réplicas, invisível nesta métrica).
        sustained_levels={
            hpa.METRIC_MEMORY: (
                hpa.HPA_TARGET_UTILIZATION * hpa.MEMORY_REQUEST_BYTES,
                hpa.MEMORY_REQUEST_BYTES,
            )
        },
    )
    ticks = _replay(mon, hpa.START_TIME + timedelta(minutes=30), hpa.RESTART_AT)
    esc = sum(1 for t in ticks if t.should_escalate and t.timestamp < hpa.MAXED_OUT_AT)
    return _summarize("hpa_scenario", seed, ticks, hpa.MAXED_OUT_AT, esc)


def _write_series_csv(path: Path, series: bp.Series) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "service", "metric_name", "value"])
        for i, v in enumerate(series.values):
            ts = bp.T0 + timedelta(minutes=i * bp.SAMPLE_MINUTES)
            w.writerow([ts.isoformat(), "svc", series.metric_name, repr(v)])


def replay_synthetic(kind: str, seed: int, workdir: Path) -> dict:
    series = bp.generate_series(kind, seed)
    path = workdir / f"{kind}_{seed}.csv"
    _write_series_csv(path, series)
    # Limiar do EWMA por métrica: 90% do teto conhecido; sem teto (memória em
    # %, cpu em %), 90 (= 90% de 100).
    threshold = 0.9 * series.ceiling if series.ceiling else 90.0
    ceilings = {series.metric_name: series.ceiling} if series.ceiling else None
    mon = ContinuousMonitor(
        CsvMetricsSource(path),
        metrics=[series.metric_name],
        ewma_threshold=threshold,
        escalate_eta_minutes=SYNTH_ESCALATE_ETA_MIN,
        ceilings=ceilings,
    )
    ticks = _replay(
        mon,
        bp.T0 + timedelta(minutes=SYNTH_FIRST_TICK),
        bp.T0 + timedelta(minutes=bp.SERIES_LEN - 1),
    )
    failure_at = (
        bp.T0 + timedelta(minutes=series.failure_index * bp.SAMPLE_MINUTES)
        if series.failure_index is not None
        else None
    )
    esc = sum(
        1
        for t in ticks
        if t.should_escalate and (failure_at is None or t.timestamp < failure_at)
    )
    return _summarize(kind, seed, ticks, failure_at, esc)


def run_backtest(seeds: int = 5, base_seed: int = 42) -> dict:
    """Executa o backtest e devolve o relatório agregado (JSON-serializável)."""
    with tempfile.TemporaryDirectory(prefix="vb4_") as tmp:
        work = Path(tmp)
        rows = [replay_hpa(base_seed + s, work) for s in range(seeds)]
        for kind in POSITIVE_SYNTH + NEGATIVE_SYNTH + EXTRA_SYNTH:
            rows += [replay_synthetic(kind, base_seed + s, work) for s in range(seeds)]

    pos = [r for r in rows if r["positive"]]
    neg = [r for r in rows if not r["positive"] and r["kind"] in NEGATIVE_SYNTH]
    extra = [r for r in rows if r["kind"] in EXTRA_SYNTH]
    tp = sum(1 for r in pos if r["first_alert_offset_min"] is not None)
    fp = sum(1 for r in neg if r["alert_ticks"] > 0)
    hpa_leads = [
        r["lead_time_min"]
        for r in rows
        if r["kind"] == "hpa_scenario" and r["lead_time_min"] is not None
    ]
    leads = [r["lead_time_min"] for r in pos if r["lead_time_min"] is not None]
    per_kind = {
        k: {
            "series": sum(1 for r in rows if r["kind"] == k),
            "detected": sum(
                1
                for r in rows
                if r["kind"] == k and r["first_alert_offset_min"] is not None
            ),
        }
        for k in dict.fromkeys(r["kind"] for r in rows)
    }
    return {
        "subsystem": "continuous_backtest",
        "scope": "tick a tick via CsvMetricsSource + ContinuousMonitor (sem LLM)",
        "config": {
            "seeds": seeds,
            "base_seed": base_seed,
            "hpa_escalate_eta_minutes": HPA_ESCALATE_ETA_MIN,
            "synthetic_escalate_eta_minutes": SYNTH_ESCALATE_ETA_MIN,
            "synthetic_series_len": bp.SERIES_LEN,
        },
        "metrics": {
            "recall": tp / len(pos) if pos else 0.0,
            "precision": tp / (tp + fp) if (tp + fp) else 1.0,
            "hpa_lead_time_min_min": min(hpa_leads, default=0.0),
            # média só sobre as seeds detectadas (non-None)
            "hpa_lead_time_min_mean": (
                sum(hpa_leads) / len(hpa_leads) if hpa_leads else 0.0
            ),
            "mean_lead_time_min": sum(leads) / len(leads) if leads else 0.0,
            "true_positives": tp,
            "false_negatives": len(pos) - tp,
            "false_positives": fp,
            "negative_alert_ticks": sum(r["alert_ticks"] for r in neg),
            "hpa_recall": per_kind["hpa_scenario"]["detected"] / seeds
            if seeds
            else 0.0,
            "noise_heavy_alert_series": sum(1 for r in extra if r["alert_ticks"] > 0),
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
        f"lead_hpa_min={m['hpa_lead_time_min_min']:.0f}min "
        f"lead_medio={m['mean_lead_time_min']:.1f}min "
        f"alertas_negativos={m['negative_alert_ticks']} "
        f"noise_heavy_series_com_alerta={m['noise_heavy_alert_series']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
