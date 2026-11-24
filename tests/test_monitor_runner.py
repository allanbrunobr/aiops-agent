"""Runner --monitor (OP2): replay CSV, exit codes e --no-llm."""

from __future__ import annotations

import csv
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from data import hpa_saturation_scenario as hpa
from domain.incident_state import IncidentState
from interfaces.cli import monitor_runner
from interfaces.cli.args import _validate_args, build_arg_parser
from interfaces.cli.exit_codes import (
    EXIT_APPROVAL_NEEDED,
    EXIT_ERROR,
    EXIT_SUCCESS,
)

pytestmark = pytest.mark.unit


def _args(argv: list[str]) -> Any:
    parser = build_arg_parser()
    args = parser.parse_args(["--monitor", *argv])
    _validate_args(parser, args)
    return args


@pytest.fixture
def stub_diagnose(monkeypatch: pytest.MonkeyPatch) -> list[bool]:
    """Troca o diagnóstico profundo (V1) por stub e registra no_llm."""
    calls: list[bool] = []

    def fake_make(cfg: Any, *, no_llm: bool = True) -> Any:
        def _diag(state: IncidentState) -> IncidentState:
            calls.append(no_llm)
            return state

        return _diag

    monkeypatch.setattr("app.monitor_setup.make_blueprint_diagnose", fake_make)
    return calls


@pytest.fixture
def hpa_csv(tmp_path: Path) -> Path:
    return hpa.export_csv(tmp_path / "hpa.csv", seed=42)


def _flat_csv(path: Path) -> Path:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "service", "metric_name", "value"])
        for i in range(120):
            ts = (hpa.START_TIME + timedelta(minutes=i)).isoformat()
            w.writerow([ts, "svc", "cpu_usage", repr(40.0)])
    return path


def test_hpa_csv_escalates_before_maxed_out_exit_3(
    hpa_csv: Path,
    stub_diagnose: list[bool],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    seen: list[Any] = []
    orig = monitor_runner._PrintingMonitor.run_for

    def spy(self: Any, ticks: int) -> Any:
        try:
            return orig(self, ticks)
        finally:
            seen.extend(self.escalations)

    monkeypatch.setattr(monitor_runner._PrintingMonitor, "run_for", spy)
    code = monitor_runner.run_monitor(
        _args(["--source", f"csv://{hpa_csv}", "--no-llm"])
    )
    assert code == EXIT_APPROVAL_NEEDED
    assert seen, "esperava ao menos uma escalada"
    first = seen[0]
    assert first.tick.timestamp.replace(tzinfo=None) < hpa.MAXED_OUT_AT
    assert first.executed_infra is False
    assert first.requires_human_approval is True
    assert first.verification_mode == "simulated"
    assert "escalar=SIM" in capsys.readouterr().out


def test_no_llm_flag_is_forwarded(hpa_csv: Path, stub_diagnose: list[bool]) -> None:
    monitor_runner.run_monitor(_args(["--source", f"csv://{hpa_csv}", "--no-llm"]))
    assert stub_diagnose and all(flag is True for flag in stub_diagnose)
    stub_diagnose.clear()
    monitor_runner.run_monitor(_args(["--source", f"csv://{hpa_csv}"]))
    assert stub_diagnose and all(flag is False for flag in stub_diagnose)


def test_flat_csv_is_clean_exit_0(tmp_path: Path, stub_diagnose: list[bool]) -> None:
    flat = _flat_csv(tmp_path / "flat.csv")
    code = monitor_runner.run_monitor(
        _args(["--source", f"csv://{flat}", "--ticks", "10", "--no-llm"])
    )
    assert code == EXIT_SUCCESS
    assert stub_diagnose == []


def test_short_ticks_on_hpa_is_clean(hpa_csv: Path, stub_diagnose: list[bool]) -> None:
    code = monitor_runner.run_monitor(
        _args(["--source", f"csv://{hpa_csv}", "--ticks", "5", "--no-llm"])
    )
    assert code == EXIT_SUCCESS


@pytest.mark.parametrize(
    "source",
    ["csv:///nao/existe.csv", "ftp://x", "prometheus://", "semscheme"],
)
def test_invalid_source_exit_1(source: str, stub_diagnose: list[bool]) -> None:
    # Sem _validate_args: o runner também falha fechado por conta própria.
    args = build_arg_parser().parse_args(["--monitor", "--source", source])
    assert monitor_runner.run_monitor(args) == EXIT_ERROR


def test_csv_missing_columns_exit_1(tmp_path: Path, stub_diagnose: list[bool]) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text("a,b\n1,2\n")
    assert monitor_runner.run_monitor(_args(["--source", f"csv://{bad}"])) == EXIT_ERROR


def test_keyboard_interrupt_during_startup_returns_clean(
    monkeypatch: pytest.MonkeyPatch, stub_diagnose: list[bool]
) -> None:
    """Ctrl+C antes de o monitor existir: saída limpa, sem UnboundLocalError."""

    def boom(_source: str) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(monitor_runner, "_build_source", boom)
    args = build_arg_parser().parse_args(["--monitor", "--source", "csv:///x.csv"])
    assert monitor_runner.run_monitor(args) == EXIT_SUCCESS


def _capture_kwargs(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    orig = monitor_runner._PrintingMonitor.__init__

    def spy(self: Any, source: Any, **kwargs: Any) -> None:
        captured.update(kwargs)
        orig(self, source, **kwargs)

    monkeypatch.setattr(monitor_runner._PrintingMonitor, "__init__", spy)
    return captured


def test_profile_generic_does_not_apply_hpa_tuning(
    hpa_csv: Path, stub_diagnose: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_kwargs(monkeypatch)
    monitor_runner.run_monitor(
        _args(["--source", f"csv://{hpa_csv}", "--no-llm", "--profile", "generic"])
    )
    assert "ewma_threshold" not in captured
    assert "sustained_levels" not in captured


def test_profile_auto_applies_hpa_tuning_on_hpa_csv(
    hpa_csv: Path, stub_diagnose: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_kwargs(monkeypatch)
    monitor_runner.run_monitor(_args(["--source", f"csv://{hpa_csv}", "--no-llm"]))
    assert captured["ewma_threshold"] == 0.9 * hpa.MEMORY_REQUEST_BYTES


def test_profile_hpa_forces_tuning_on_non_hpa_csv(
    tmp_path: Path, stub_diagnose: list[bool], monkeypatch: pytest.MonkeyPatch
) -> None:
    flat = _flat_csv(tmp_path / "flat.csv")
    captured = _capture_kwargs(monkeypatch)
    monitor_runner.run_monitor(
        _args(["--source", f"csv://{flat}", "--no-llm", "--profile", "hpa"])
    )
    assert captured["ewma_threshold"] == 0.9 * hpa.MEMORY_REQUEST_BYTES
    captured.clear()
    monitor_runner.run_monitor(_args(["--source", f"csv://{flat}", "--no-llm"]))
    assert "ewma_threshold" not in captured
