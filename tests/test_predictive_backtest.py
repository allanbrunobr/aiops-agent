from __future__ import annotations

"""Backtest preditivo (D5): recall/precision/lead-time com seed fixa."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.integration]

_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "backtest_predictive", _ROOT / "scripts" / "backtest_predictive.py"
)
assert _SPEC and _SPEC.loader
bt = importlib.util.module_from_spec(_SPEC)
sys.modules["backtest_predictive"] = bt
_SPEC.loader.exec_module(bt)


@pytest.fixture(scope="module")
def report() -> dict:
    return bt.run_backtest(seeds=5, base_seed=42)


def test_recall_nas_falhas_fortes(report):
    assert report["metrics"]["recall"] >= 0.8


def test_precision_minima(report):
    assert report["metrics"]["precision"] >= 0.7


def test_zero_alertas_em_flat_e_ruido(report):
    for row in report["series"]:
        if row["kind"] in ("flat", "noise"):
            assert row["alert_windows"] == 0, row


def test_lead_time_positivo_nas_detectadas(report):
    leads = [r["lead_time_min"] for r in report["series"] if r["failure_index"]]
    assert all(lead is not None and lead > 0 for lead in leads)
    assert report["metrics"]["mean_lead_time_min"] > 0


def test_determinismo_e_golden_atualizado(report):
    again = bt.run_backtest(seeds=5, base_seed=42)
    assert again == report
    golden = json.loads(bt.GOLDEN_PATH.read_text(encoding="utf-8"))
    assert golden == json.loads(json.dumps(report))


def test_instrumento_distingue_detector_cego(monkeypatch):
    """Detector que nunca alerta → recall 0 (o medidor não dá nota máxima a tudo)."""
    monkeypatch.setattr(
        bt, "run_predictive_stage", lambda *a, **k: SimpleNamespace(event_level=None)
    )
    assert bt.run_backtest(seeds=2)["metrics"]["recall"] == 0.0


def test_instrumento_distingue_detector_alarmista(monkeypatch):
    """Detector que sempre alerta → falsos positivos em flat/ruído."""
    monkeypatch.setattr(
        bt,
        "run_predictive_stage",
        lambda *a, **k: SimpleNamespace(event_level="prediction.alert"),
    )
    m = bt.run_backtest(seeds=2)["metrics"]
    assert m["false_positives"] > 0
    assert m["precision"] < 0.7


def test_cli_grava_so_no_destino_pedido(tmp_path):
    out = tmp_path / "bt.json"
    assert bt.main(["--seeds", "1", "--output", str(out)]) == 0
    assert json.loads(out.read_text())["subsystem"] == "predictive_backtest"
