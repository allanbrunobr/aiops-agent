"""Pós-revisão (menores): paridade de validação de parâmetros nos fallbacks Python.

As bridges Rust (``rust/src/metric_filter/bridge.rs``,
``rust/src/trace_filter/bridge.rs``) rejeitam parâmetros inválidos com
``ValueError``. Os fallbacks Python (``_detect_python`` / ``_filter_python``)
aceitavam silenciosamente ``sigma_threshold=0``, ``baseline_ratio=1.0``,
``percentile_threshold=101``, ``max_hops=0`` — divergência de paridade quando a
extensão Rust não está disponível. Estes testes forçam o caminho Python
(``_RUST_AVAILABLE=False``) e exigem as mesmas mensagens da bridge.
"""

from __future__ import annotations

import pytest

import pipeline.metric_filter as mf
import pipeline.trace_filter as tf


@pytest.fixture
def force_python(monkeypatch):
    monkeypatch.setattr(mf, "_RUST_AVAILABLE", False)
    monkeypatch.setattr(tf, "_RUST_AVAILABLE", False)


@pytest.mark.unit
@pytest.mark.parametrize("sigma", [0, -1, -3.5, float("nan"), float("inf")])
def test_metric_python_rejects_bad_sigma(force_python, sigma):
    with pytest.raises(ValueError, match="sigma_threshold must be > 0"):
        mf.MetricFilter(sigma_threshold=sigma).detect_anomalies([])


@pytest.mark.unit
@pytest.mark.parametrize("ratio", [0, 1.0, 1.5, -0.2, float("nan")])
def test_metric_python_rejects_bad_baseline_ratio(force_python, ratio):
    with pytest.raises(ValueError, match=r"baseline_ratio must be in \(0, 1\)"):
        mf.MetricFilter(baseline_ratio=ratio).detect_anomalies([])


@pytest.mark.unit
def test_metric_python_accepts_valid_params(force_python):
    # Vazio → lista vazia, sem erro (validação passa).
    assert (
        mf.MetricFilter(sigma_threshold=3.0, baseline_ratio=0.5).detect_anomalies([])
        == []
    )


@pytest.mark.unit
@pytest.mark.parametrize("pct", [-1, 101, 150, float("nan")])
def test_trace_python_rejects_bad_percentile(force_python, pct):
    with pytest.raises(ValueError, match=r"percentile_threshold must be in \[0, 100\]"):
        tf.TraceFilter(percentile_threshold=pct).filter_slow_traces([])


@pytest.mark.unit
@pytest.mark.parametrize("hops", [0, -1, -5])
def test_trace_python_rejects_bad_max_hops(force_python, hops):
    with pytest.raises(ValueError, match="max_hops must be >= 1"):
        tf.TraceFilter(max_hops=hops).filter_slow_traces([])


@pytest.mark.unit
def test_trace_python_accepts_valid_params(force_python):
    assert (
        tf.TraceFilter(percentile_threshold=95.0, max_hops=3).filter_slow_traces([])
        == []
    )
