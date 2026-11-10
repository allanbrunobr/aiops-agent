"""Fallback Python do LogParser: amostrar não pode encolher as contagens (F12).

P3-4 (docs/analise-20261109.md, B15): acima de 500 logs o fallback amostra
`logs[::step]` e contava só as amostras. Com 1000 logs (step=2), 60 erros
viravam ~30 e deixavam de passar o error_threshold=50 do DetectionAgent;
com Rust, contavam 60. O mesmo incidente era detectado num ambiente e não no
outro.
"""
from __future__ import annotations

import datetime as dt

import pytest

pytestmark = pytest.mark.unit


def _logs(total: int, errors: int):
    from data.models import LogEntry

    t0 = dt.datetime(2026, 1, 1, 12, 0, 0)
    out = []
    for i in range(total):
        if i % (total // errors) == 0 and sum(1 for e in out if e.level == "ERROR") < errors:
            out.append(LogEntry(t0 + dt.timedelta(seconds=i), "ERROR", "payment-svc",
                                "Connection pool exhausted waiting for connection"))
        else:
            out.append(LogEntry(t0 + dt.timedelta(seconds=i), "INFO", "payment-svc",
                                "Request completed successfully"))
    return out


@pytest.mark.parametrize("total", [400, 999, 1000, 2500])
def test_python_fallback_counts_stay_in_input_scale(monkeypatch, total):
    import pipeline.log_parser as lp

    monkeypatch.setattr(lp, "_RUST_AVAILABLE", False)
    logs = _logs(total, errors=60)
    assert sum(1 for e in logs if e.level == "ERROR") == 60

    templates = lp.LogParser().parse(logs)
    errors = sum(t.count for t in templates if t.level == "ERROR")
    assert sum(t.count for t in templates) == total
    assert errors == 60, f"{total} logs: {errors} erros contados (esperado 60)"
