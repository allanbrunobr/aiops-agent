"""Smoke da demo contínua (OP3): detecta + escala antes de MAXED_OUT_AT."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from data import hpa_saturation_scenario as hpa
from domain.incident_state import IncidentState

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "run_continuous_demo.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("run_continuous_demo", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_continuous_demo"] = mod
    spec.loader.exec_module(mod)
    return mod


def _stub(state: IncidentState) -> IncidentState:
    return state


def test_demo_detects_and_escalates_before_maxed_out(tmp_path: Path) -> None:
    demo = _load()
    result = demo.run_demo(seed=42, workdir=tmp_path, diagnose=_stub)
    assert result["first_alert_at"] < hpa.MAXED_OUT_AT
    assert result["first_escalation_at"] < hpa.MAXED_OUT_AT
    assert result["lead_time_min"] > 0
    assert result["escalations"] >= 1
    assert result["executed_infra"] is False
    assert result["verification_mode"] == "simulated"


def test_demo_main_prints_timeline_exit_3(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    demo = _load()
    code = demo.main(["--seed", "42", "--workdir", str(tmp_path)], diagnose=_stub)
    out = capsys.readouterr().out
    assert code == 3
    assert "lead time" in out.lower()
    assert "simulated" in out.lower()


def test_demo_csv_source_override(tmp_path: Path) -> None:
    demo = _load()
    csv_path = hpa.export_csv(tmp_path / "own.csv", seed=7)
    result = demo.run_demo(source=f"csv://{csv_path}", workdir=tmp_path, diagnose=_stub)
    assert result["lead_time_min"] > 0
    assert result["executed_infra"] is False


def test_demo_invalid_source_exit_1(tmp_path: Path) -> None:
    demo = _load()
    assert demo.main(["--source", "http://x", "--workdir", str(tmp_path)]) == 1
