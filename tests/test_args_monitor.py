"""Flags --monitor/--source/--interval/--ticks/--monitor-service (OP1)."""

from __future__ import annotations

import argparse

import pytest

from interfaces.cli.args import _validate_args, build_arg_parser

pytestmark = pytest.mark.unit


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)
    return args


def _fails(argv: list[str], capsys: pytest.CaptureFixture[str]) -> str:
    with pytest.raises(SystemExit) as exc:
        _parse(argv)
    assert exc.value.code == 1
    return capsys.readouterr().err


def test_defaults_without_monitor() -> None:
    args = _parse([])
    assert args.monitor is False
    assert args.source is None
    assert args.interval == 60.0
    assert args.ticks is None
    assert args.monitor_service == "hpa-demo"


def test_monitor_flags_parse() -> None:
    args = _parse(
        [
            "--monitor",
            "--source",
            "csv:///tmp/x.csv",
            "--interval",
            "0.5",
            "--ticks",
            "3",
            "--monitor-service",
            "svc",
        ]
    )
    assert args.monitor is True
    assert args.source == "csv:///tmp/x.csv"
    assert args.interval == 0.5
    assert args.ticks == 3
    assert args.monitor_service == "svc"


def test_prometheus_scheme_accepted() -> None:
    assert _parse(["--monitor", "--source", "prometheus://http://p:9090"]).monitor


@pytest.mark.parametrize(
    "extra",
    [
        ["--scenario", "payment-pool-exhaustion"],
        ["--data-source", "databricks"],
        ["--tracker", "jira"],
    ],
)
def test_monitor_mutually_exclusive(
    extra: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    err = _fails(["--monitor", "--source", "csv://a.csv", *extra], capsys)
    assert "--monitor" in err


def test_monitor_requires_source(capsys: pytest.CaptureFixture[str]) -> None:
    assert "--source" in _fails(["--monitor"], capsys)


@pytest.mark.parametrize("src", ["http://x", "csv:", "csv://", "nada", "prometheus://"])
def test_invalid_source_scheme(src: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert "--source" in _fails(["--monitor", "--source", src], capsys)


@pytest.mark.parametrize("interval", ["0", "-1"])
def test_interval_must_be_positive(
    interval: str, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--monitor", "--source", "csv://a.csv", "--interval", interval]
    assert "--interval" in _fails(argv, capsys)


@pytest.mark.parametrize("ticks", ["0", "-2"])
def test_ticks_must_be_positive(ticks: str, capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["--monitor", "--source", "csv://a.csv", "--ticks", ticks]
    assert "--ticks" in _fails(argv, capsys)


def test_profile_default_and_values() -> None:
    assert _parse([]).profile == "auto"
    for value in ("auto", "hpa", "generic"):
        argv = ["--monitor", "--source", "csv://x.csv", "--profile", value]
        assert _parse(argv).profile == value


def test_profile_invalid_rejected() -> None:
    with pytest.raises(SystemExit):
        build_arg_parser().parse_args(["--monitor", "--profile", "bogus"])
