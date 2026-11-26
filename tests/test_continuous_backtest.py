from __future__ import annotations

"""Backtest do monitor contínuo (VB4): lead time, recall, zero FP em flat/ruído."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import pipeline.continuous_monitor as cm

pytestmark = [pytest.mark.slow, pytest.mark.integration]

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "backtest_continuous", _ROOT / "scripts" / "backtest_continuous.py"
)
assert _SPEC and _SPEC.loader
bt = importlib.util.module_from_spec(_SPEC)
sys.modules["backtest_continuous"] = bt
_SPEC.loader.exec_module(bt)


@pytest.fixture(scope="module")
def report() -> dict:
    return bt.run_backtest(seeds=50, base_seed=42)


def _row(report: dict, kind: str, seed: int) -> dict:
    return next(r for r in report["series"] if r["kind"] == kind and r["seed"] == seed)


def test_hpa_canonico_lead_time_positivo(report):
    row = _row(report, "hpa_scenario", 42)
    assert row["lead_time_min"] is not None and row["lead_time_min"] > 0
    assert report["metrics"]["hpa_lead_time_min_min"] > 0


def test_recall_minimo(report):
    assert report["metrics"]["recall"] >= 0.8


def test_lead_time_positivo_em_todos_os_detectados(report):
    leads = [r["lead_time_min"] for r in report["series"] if r["positive"]]
    assert all(lead > 0 for lead in leads if lead is not None)
    assert report["metrics"]["mean_lead_time_min"] > 0


def test_zero_alertas_em_flat_e_ruido_puro(report):
    for r in report["series"]:
        if r["kind"] in ("flat", "noise"):
            assert r["alert_ticks"] == 0 and r["escalate_ticks"] == 0, r
    assert report["metrics"]["false_positives"] == 0
    assert report["metrics"]["precision"] == 1.0


def test_hpa_recall_alto_com_lead_time_positivo(report):
    """VB4-FIX: nível crônico acima do alvo 80% captura o creep lento."""
    m = report["metrics"]
    assert m["hpa_recall"] >= 0.8
    k = report["per_kind"]["hpa_scenario"]
    assert k["detected"] == round(m["hpa_recall"] * k["series"])
    for r in report["series"]:
        if r["kind"] == "hpa_scenario" and r["first_alert_offset_min"] is not None:
            assert r["lead_time_min"] > 0
    assert m["recall"] >= 0.8
    assert m["precision"] == 1.0


def test_golden_com_50_seeds_e_variancia(report):
    assert report["config"]["seeds"] == 50
    m = report["metrics"]
    assert m["recall"] >= 0.8 and m["precision"] >= 0.9
    lo, hi = m["hpa_lead_time_min_min"], m["hpa_lead_time_min_max"]
    mean, std = m["hpa_lead_time_min_mean"], m["hpa_lead_time_min_std"]
    assert 0 < lo <= mean <= hi and std >= 0
    for kind in ("flat", "noise", "noise_heavy"):
        assert report["per_kind"][kind]["false_alarm_rate"] == 0.0


def test_pstdev_populacional():
    assert bt._pstdev([]) == 0.0
    assert bt._pstdev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]) == 2.0


def test_hpa_lead_time_mean_nao_menor_que_min(report):
    """Regressão: média dividia pelo total de seeds (incluía None) e ficava < min."""
    m = report["metrics"]
    assert m["hpa_lead_time_min_mean"] >= m["hpa_lead_time_min_min"] > 0


def test_hpa_lead_time_mean_ignora_seeds_nao_detectadas(monkeypatch):
    """Com 1 detecção e 1 miss, a média é o lead da detectada (não metade)."""
    rows = iter([60.0, None])

    def fake_hpa(seed, work):
        lead = next(rows)
        return {
            "kind": "hpa_scenario",
            "seed": seed,
            "ticks": 1,
            "first_alert_offset_min": None if lead is None else 1.0,
            "lead_time_min": lead,
            "alert_ticks": 0 if lead is None else 1,
            "escalate_ticks": 0,
            "positive": True,
        }

    monkeypatch.setattr(bt, "replay_hpa", fake_hpa)
    monkeypatch.setattr(
        bt,
        "replay_synthetic",
        lambda k, s, w: {
            "kind": k,
            "seed": s,
            "ticks": 1,
            "first_alert_offset_min": None,
            "lead_time_min": None,
            "alert_ticks": 0,
            "escalate_ticks": 0,
            "positive": k in bt.POSITIVE_SYNTH,
        },
    )
    m = bt.run_backtest(seeds=2)["metrics"]
    assert m["hpa_lead_time_min_mean"] == m["hpa_lead_time_min_min"] == 60.0


def test_determinismo_e_golden_atualizado(report):
    assert bt.run_backtest(seeds=50, base_seed=42) == report
    golden = json.loads(bt.GOLDEN_PATH.read_text(encoding="utf-8"))
    assert golden == json.loads(json.dumps(report))


def test_instrumento_distingue_monitor_cego(monkeypatch):
    """Monitor que nunca avisa → recall 0 (o medidor não dá nota máxima a tudo)."""
    monkeypatch.setattr(cm, "get_event_level", lambda *a, **k: None)
    assert bt.run_backtest(seeds=2)["metrics"]["recall"] == 0.0


def test_instrumento_distingue_monitor_alarmista(monkeypatch):
    """Monitor que sempre avisa → falsos positivos e precision cai."""
    monkeypatch.setattr(cm, "get_event_level", lambda *a, **k: "prediction.alert")
    m = bt.run_backtest(seeds=2)["metrics"]
    assert m["false_positives"] > 0
    assert m["precision"] < 0.7


def test_cli_grava_so_no_destino_pedido(tmp_path):
    out = tmp_path / "cb.json"
    assert bt.main(["--seeds", "1", "--output", str(out)]) == 0
    assert json.loads(out.read_text())["subsystem"] == "continuous_backtest"
