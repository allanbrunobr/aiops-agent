"""Extensão Rust antiga não derruba o pipeline (revisão da P3-4).

Com a wheel anterior à P3-4, `filter_metrics(metrics, 3.0, 0.5)` dava
TypeError ("takes 1 positional arguments but 3 were given") no caminho padrão
da CLI — medido rodando main.py com a wheel antiga no PYTHONPATH.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def _fn(signature):
    def f(*args):
        return []
    f.__text_signature__ = signature
    return f


def test_old_signature_counts_as_unavailable(caplog):
    from pipeline.rust_compat import has_params

    old = SimpleNamespace(filter_metrics=_fn("(metrics)"))
    assert not has_params(old, "filter_metrics", "sigma_threshold", "baseline_ratio")
    assert "build_rust.sh" in caplog.text


def test_new_signature_is_available():
    from pipeline.rust_compat import has_params

    new = SimpleNamespace(filter_traces=_fn("(spans, percentile_threshold, max_hops)"))
    assert has_params(new, "filter_traces", "percentile_threshold", "max_hops")


def test_missing_function_is_unavailable():
    from pipeline.rust_compat import has_params

    assert not has_params(SimpleNamespace(), "filter_metrics", "sigma_threshold")
