"""
Gerador de cenário fictício: KubeHpaMaxedOut (wbia/prd).

Cenário: creep de memória → utilização ``working_set / request`` crônica acima
do alvo de 80% do HPA → réplicas sobem até o teto ``maxReplicas=10`` e ficam
presas (platô). Restart é paliativo: a memória cai e volta a subir.

Quatro fases (1 amostra/min, ~19h, 1140 amostras, seed fixa):
  1. normal      (~2h)  — working_set ~300Mi, réplicas=2
  2. creep       (~10h) — 300Mi→465Mi; ao cruzar 80%, réplicas sobem 2→10
  3. saturação   (~6h)  — réplicas presas em 10, util crônica 82–92%
  4. restart     (~1h)  — working_set cai e volta a subir; réplicas no teto

Ground-truth: ``incident_start`` (início do creep), ``maxed_out_at`` (início
da saturação; ``KubeHpaMaxedOut`` dispara) e ``restart_at``.

Módulo stdlib-only — não importa dependências pesadas (torch, faiss, etc.).
"""

from __future__ import annotations

import csv
import datetime
import random
from dataclasses import dataclass
from pathlib import Path

from data.models import LogEntry, MetricPoint, SimulatedIncident
from data.registry import register_scenario

SERVICE = "wbia"
NAMESPACE = "prd"

MEMORY_REQUEST_BYTES = 512 * 1024**2  # 512Mi
HPA_TARGET_UTILIZATION = 0.80
MAX_REPLICAS = 10
MIN_REPLICAS = 2
SAMPLE_INTERVAL_S = 60

_MI = 1024**2
_NORMAL_SAMPLES = 120  # ~2h
_CREEP_SAMPLES = 600  # ~10h
_SATURATION_SAMPLES = 360  # ~6h
_RESTART_SAMPLES = 60  # ~1h
TOTAL_SAMPLES = (
    _NORMAL_SAMPLES + _CREEP_SAMPLES + _SATURATION_SAMPLES + _RESTART_SAMPLES
)

# Alerta real ~18h47 de 22/11; a saturação começa nesse instante.
MAXED_OUT_AT = datetime.datetime(2026, 11, 22, 18, 47, 0)
INCIDENT_START = MAXED_OUT_AT - datetime.timedelta(
    seconds=_CREEP_SAMPLES * SAMPLE_INTERVAL_S
)
START_TIME = INCIDENT_START - datetime.timedelta(
    seconds=_NORMAL_SAMPLES * SAMPLE_INTERVAL_S
)
RESTART_AT = MAXED_OUT_AT + datetime.timedelta(
    seconds=_SATURATION_SAMPLES * SAMPLE_INTERVAL_S
)

_NORMAL_MI = 300.0
_CREEP_END_MI = 465.0
_SAT_END_MI = 470.0
_UTIL_CAP = 0.92
_UTIL_FLOOR_SAT = 0.82

METRIC_MEMORY = "container_memory_working_set_bytes"
METRIC_CPU = "container_cpu_usage_seconds"
METRIC_REPLICAS = "kube_hpa_status_current_replicas"
METRIC_MAX_REPLICAS = "kube_hpa_spec_max_replicas"


@dataclass(frozen=True)
class HpaGroundTruth:
    """Rótulos do cenário, para medir lead time e precisão."""

    incident_start: datetime.datetime
    maxed_out_at: datetime.datetime
    restart_at: datetime.datetime


def ground_truth() -> HpaGroundTruth:
    return HpaGroundTruth(INCIDENT_START, MAXED_OUT_AT, RESTART_AT)


def _working_set_mi(i: int, rng: random.Random) -> float:
    """Working set por pod (Mi) na amostra ``i``."""
    if i < _NORMAL_SAMPLES:
        return _NORMAL_MI + rng.gauss(0, 2.0)
    j = i - _NORMAL_SAMPLES
    if j < _CREEP_SAMPLES:
        frac = j / (_CREEP_SAMPLES - 1)
        return _NORMAL_MI + frac * (_CREEP_END_MI - _NORMAL_MI) + rng.gauss(0, 2.5)
    k = j - _CREEP_SAMPLES
    if k < _SATURATION_SAMPLES:
        frac = k / (_SATURATION_SAMPLES - 1)
        value = _CREEP_END_MI + frac * (_SAT_END_MI - _CREEP_END_MI)
        value += rng.gauss(0, 2.0)
        lo = _UTIL_FLOOR_SAT * MEMORY_REQUEST_BYTES / _MI
        hi = _UTIL_CAP * MEMORY_REQUEST_BYTES / _MI
        return min(hi, max(lo, value))
    r = k - _SATURATION_SAMPLES
    return _NORMAL_MI + 30.0 * r / (_RESTART_SAMPLES - 1) + rng.gauss(0, 2.0)


def _replica_schedule() -> list[int]:
    """Réplicas por amostra: degraus 2→10 após util cruzar 80%, nunca desce."""
    steps = MAX_REPLICAS - MIN_REPLICAS
    cross_frac = (HPA_TARGET_UTILIZATION * MEMORY_REQUEST_BYTES / _MI - _NORMAL_MI) / (
        _CREEP_END_MI - _NORMAL_MI
    )
    cross_j = int(cross_frac * (_CREEP_SAMPLES - 1))
    span = (_CREEP_SAMPLES - 1) - cross_j
    out: list[int] = []
    for i in range(TOTAL_SAMPLES):
        j = i - _NORMAL_SAMPLES
        if j < cross_j:
            out.append(MIN_REPLICAS)
        elif j >= _CREEP_SAMPLES - 1:
            out.append(MAX_REPLICAS)
        else:
            out.append(MIN_REPLICAS + 1 + (j - cross_j) * (steps - 1) // span)
    return out


def generate_metrics(seed: int = 42) -> list[MetricPoint]:
    """Gera a série temporal do cenário (determinística por ``seed``)."""
    rng = random.Random(seed)
    replicas = _replica_schedule()
    metrics: list[MetricPoint] = []
    for i in range(TOTAL_SAMPLES):
        ts = START_TIME + datetime.timedelta(seconds=i * SAMPLE_INTERVAL_S)
        ws_bytes = max(0.0, _working_set_mi(i, rng)) * _MI
        cpu = max(0.0, 0.30 + 0.02 * replicas[i] + rng.gauss(0, 0.02))
        metrics.append(MetricPoint(ts, SERVICE, METRIC_MEMORY, ws_bytes))
        metrics.append(MetricPoint(ts, SERVICE, METRIC_CPU, cpu))
        metrics.append(MetricPoint(ts, SERVICE, METRIC_REPLICAS, float(replicas[i])))
        metrics.append(
            MetricPoint(ts, SERVICE, METRIC_MAX_REPLICAS, float(MAX_REPLICAS))
        )
    return metrics


def generate_logs() -> list[LogEntry]:
    """Poucos eventos-chave (HPA/alerta/restart), determinísticos."""
    return [
        LogEntry(
            INCIDENT_START,
            "WARN",
            SERVICE,
            f"{NAMESPACE}/{SERVICE}: working_set growing steadily (possible memory creep)",
        ),
        LogEntry(
            MAXED_OUT_AT,
            "ERROR",
            "horizontal-pod-autoscaler",
            f"KubeHpaMaxedOut: HPA {NAMESPACE}/{SERVICE} running at max replicas ({MAX_REPLICAS})",
        ),
        LogEntry(
            RESTART_AT,
            "INFO",
            SERVICE,
            f"{NAMESPACE}/{SERVICE}: pods restarted manually (palliative), memory dropped",
        ),
    ]


def export_csv(path: str | Path, seed: int = 42) -> Path:
    """Grava ``timestamp,service,metric_name,value`` (um ponto por linha)."""
    out = Path(path)
    with out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["timestamp", "service", "metric_name", "value"])
        for p in generate_metrics(seed):
            writer.writerow(
                [p.timestamp.isoformat(), p.service, p.metric_name, repr(p.value)]
            )
    return out


def generate_hpa_saturation(seed: int = 42) -> SimulatedIncident:
    """Cenário KubeHpaMaxedOut. Rótulos: deploy_time=incident_start,
    spike_start=detection_time=maxed_out_at, fix_time=restart_at."""
    return SimulatedIncident(
        logs=generate_logs(),
        metrics=generate_metrics(seed),
        traces=[],
        deploy_time=INCIDENT_START,
        spike_start=MAXED_OUT_AT,
        detection_time=MAXED_OUT_AT,
        fix_time=RESTART_AT,
        actual_root_cause=(
            "Memory creep in wbia keeps working_set/request above the 80% HPA "
            "target; replicas scale up to maxReplicas=10 and stay pinned "
            "(KubeHpaMaxedOut)."
        ),
        actual_fix=(
            "Raise the memory request (denominator) and fix the leak; "
            "restart is only palliative."
        ),
    )


register_scenario("hpa-saturation", generate_hpa_saturation)
