"""Testes do cenário fictício KubeHpaMaxedOut (wbia/prd) — VB-T1."""

from __future__ import annotations

import ast
import csv
import datetime
import statistics
from pathlib import Path

import pytest

import data.hpa_saturation_scenario as hpa
from data.models import MetricPoint, SimulatedIncident
from data.registry import get_scenario, list_scenarios

pytestmark = pytest.mark.unit


def _series(points: list[MetricPoint], name: str) -> list[MetricPoint]:
    return [p for p in points if p.metric_name == name]


def test_registered_and_returns_incident() -> None:
    assert "hpa-saturation" in list_scenarios()
    incident = get_scenario("hpa-saturation")(42)
    assert isinstance(incident, SimulatedIncident)
    assert incident.metrics


def test_sample_count_and_metric_names() -> None:
    metrics = hpa.generate_metrics()
    names = {p.metric_name for p in metrics}
    assert names == {
        "container_memory_working_set_bytes",
        "container_cpu_usage_seconds",
        "kube_hpa_status_current_replicas",
        "kube_hpa_spec_max_replicas",
    }
    assert {p.service for p in metrics} == {"wbia"}
    reps = _series(metrics, "kube_hpa_status_current_replicas")
    assert 1100 <= len(reps) <= 1180
    gap = reps[1].timestamp - reps[0].timestamp
    assert gap == datetime.timedelta(seconds=hpa.SAMPLE_INTERVAL_S)


def test_deterministic_by_seed() -> None:
    assert hpa.generate_metrics(seed=7) == hpa.generate_metrics(seed=7)
    assert hpa.generate_metrics(seed=7) != hpa.generate_metrics(seed=8)


def test_labels_ordered_and_inside_series() -> None:
    gt = hpa.ground_truth()
    metrics = hpa.generate_metrics()
    first, last = metrics[0].timestamp, metrics[-1].timestamp
    assert first < gt.incident_start < gt.maxed_out_at < gt.restart_at <= last
    assert gt.incident_start - first == datetime.timedelta(hours=2)
    assert gt.maxed_out_at - gt.incident_start == datetime.timedelta(hours=10)
    assert gt.restart_at - gt.maxed_out_at == datetime.timedelta(hours=6)


def test_util_crosses_target_and_replicas_reach_ceiling() -> None:
    metrics = hpa.generate_metrics()
    gt = hpa.ground_truth()
    req = hpa.MEMORY_REQUEST_BYTES
    ws = _series(metrics, "container_memory_working_set_bytes")
    normal = [p for p in ws if p.timestamp < gt.incident_start]
    assert max(p.value for p in normal) / req < hpa.HPA_TARGET_UTILIZATION
    reps = _series(metrics, "kube_hpa_status_current_replicas")
    assert all(
        p.value == hpa.MIN_REPLICAS for p in reps if p.timestamp < gt.incident_start
    )
    creep = [p for p in ws if gt.incident_start <= p.timestamp < gt.maxed_out_at]
    assert max(p.value for p in creep) / req > hpa.HPA_TARGET_UTILIZATION
    values = [p.value for p in reps]
    assert max(values) == hpa.MAX_REPLICAS
    assert all(b >= a for a, b in zip(values, values[1:]))  # nunca scale-down
    assert set(values) == set(range(hpa.MIN_REPLICAS, hpa.MAX_REPLICAS + 1))
    first_max = next(p for p in reps if p.value == hpa.MAX_REPLICAS)
    assert first_max.timestamp <= gt.maxed_out_at


def test_final_plateau_replicas_pinned_low_variance() -> None:
    metrics = hpa.generate_metrics()
    gt = hpa.ground_truth()
    sat_reps = [
        p.value
        for p in _series(metrics, "kube_hpa_status_current_replicas")
        if p.timestamp >= gt.maxed_out_at
    ]
    assert statistics.pvariance(sat_reps) < 1e-9
    assert set(sat_reps) == {float(hpa.MAX_REPLICAS)}
    sat_util = [
        p.value / hpa.MEMORY_REQUEST_BYTES
        for p in _series(metrics, "container_memory_working_set_bytes")
        if gt.maxed_out_at <= p.timestamp < gt.restart_at
    ]
    assert min(sat_util) >= hpa.HPA_TARGET_UTILIZATION
    assert max(sat_util) <= 0.92 + 1e-9


def test_restart_drops_memory_but_replicas_stay_at_ceiling() -> None:
    metrics = hpa.generate_metrics()
    gt = hpa.ground_truth()
    ws = _series(metrics, "container_memory_working_set_bytes")
    before = [p.value for p in ws if p.timestamp < gt.restart_at][-1]
    after = next(p.value for p in ws if p.timestamp >= gt.restart_at)
    assert after < before * 0.8
    reps = [
        p.value
        for p in _series(metrics, "kube_hpa_status_current_replicas")
        if p.timestamp >= gt.restart_at
    ]
    assert set(reps) == {float(hpa.MAX_REPLICAS)}


def test_spec_max_replicas_constant() -> None:
    mx = _series(hpa.generate_metrics(), "kube_hpa_spec_max_replicas")
    assert {p.value for p in mx} == {float(hpa.MAX_REPLICAS)}


def test_incident_labels_map_to_ground_truth() -> None:
    inc = hpa.generate_hpa_saturation(42)
    gt = hpa.ground_truth()
    assert inc.deploy_time == gt.incident_start
    assert inc.spike_start == gt.maxed_out_at
    assert inc.fix_time == gt.restart_at


def test_csv_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "hpa.csv"
    hpa.export_csv(out)
    with out.open(newline="") as fh:
        reader = csv.DictReader(fh)
        assert reader.fieldnames == ["timestamp", "service", "metric_name", "value"]
        rows = list(reader)
    metrics = hpa.generate_metrics()
    assert len(rows) == len(metrics)
    back = [
        MetricPoint(
            datetime.datetime.fromisoformat(r["timestamp"]),
            r["service"],
            r["metric_name"],
            float(r["value"]),
        )
        for r in rows
    ]
    assert back == metrics


def test_stdlib_only() -> None:
    src = Path(hpa.__file__).read_text()
    heavy = {"torch", "faiss", "sentence_transformers", "numpy", "transformers"}
    roots: set[str] = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".")[0])
    assert not roots & heavy
