"""Fronteira Python↔Rust: limiares atravessam, custom_patterns não se perde (A8).

P3-4 (docs/analise-20261109.md, F5-08/ARQ-12/ARQ-13):
- `filter_metrics`/`filter_traces` tinham 3.0/0.5 e 95.0/3 fixos no Rust, e o
  Python só delegava quando a config era igual a esses literais (igualdade de
  float): mudar o YAML desligava o Rust sem aviso;
- `custom_patterns` do sanitizador era ignorado quando o Rust estava
  instalado (o caminho Rust não os recebe);
- EWMA/CUSUM estavam duplicados dentro do próprio lib.rs.
Estes testes rodam sem a extensão: um `_rust` falso registra as chamadas.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent


class _FakeRust:
    def __init__(self):
        self.calls: list[tuple] = []

    def filter_metrics(self, *args):
        self.calls.append(("filter_metrics", args))
        return []

    def filter_traces(self, *args):
        self.calls.append(("filter_traces", args))
        return []

    def detect_taint_batch(self, messages):
        self.calls.append(("detect_taint_batch", (messages,)))
        return ["clean"] * len(messages)

    def detect_taint_single(self, text):
        self.calls.append(("detect_taint_single", (text,)))
        return "clean"


@pytest.fixture
def fake_rust(monkeypatch):
    import pipeline.metric_filter as mf
    import pipeline.telemetry_sanitizer as ts
    import pipeline.trace_filter as tf

    fake = _FakeRust()
    for module in (mf, tf, ts):
        monkeypatch.setattr(module, "_rust", fake, raising=False)
        monkeypatch.setattr(module, "_RUST_AVAILABLE", True)
    return fake


def test_metric_thresholds_cross_the_boundary(fake_rust):
    from pipeline.metric_filter import MetricFilter

    import datetime as dt
    from types import SimpleNamespace

    metrics = [SimpleNamespace(timestamp=dt.datetime(2026, 1, 1))]
    MetricFilter(sigma_threshold=2.5, baseline_ratio=0.4).detect_anomalies(metrics)
    assert fake_rust.calls == [("filter_metrics", (metrics, 2.5, 0.4))]


def test_trace_thresholds_cross_the_boundary(fake_rust):
    from pipeline.trace_filter import TraceFilter

    from types import SimpleNamespace

    spans = [SimpleNamespace(trace_id="t1")]
    TraceFilter(percentile_threshold=90.0, max_hops=2).filter_slow_traces(spans)
    assert fake_rust.calls == [("filter_traces", (spans, 90.0, 2))]


def test_custom_patterns_are_not_dropped_on_the_rust_path(fake_rust):
    from pipeline.telemetry_sanitizer import TaintLevel, TelemetrySanitizer

    sanitizer = TelemetrySanitizer(custom_patterns=[r"rotate\s+the\s+kms\s+key"])
    assert sanitizer._detect_taint("please rotate the KMS key now") == TaintLevel.ADVERSARIAL
    assert not any(name.startswith("detect_taint") for name, _ in fake_rust.calls)


def test_without_custom_patterns_the_rust_path_is_used(fake_rust):
    from pipeline.telemetry_sanitizer import TelemetrySanitizer

    TelemetrySanitizer()._detect_taint("texto qualquer")
    assert fake_rust.calls == [("detect_taint_single", ("texto qualquer",))]


def test_filter_defaults_come_from_thresholds_config():
    from config.loader import ThresholdsConfig
    from pipeline.aggregator import Aggregator
    from pipeline.metric_filter import MetricFilter
    from pipeline.trace_filter import TraceFilter

    cfg = ThresholdsConfig()
    for cls in (MetricFilter, TraceFilter, Aggregator):
        for name, param in inspect.signature(cls.__init__).parameters.items():
            if hasattr(cfg, name):
                assert param.default == getattr(cfg, name), f"{cls.__name__}.{name}"


def test_lib_rs_has_no_inline_copies_and_no_threshold_literals():
    lib = (ROOT / "rust/src/lib.rs").read_text()
    assert "#[pyfunction]" not in lib, "lib.rs ainda define funções PyO3 inline"
    assert "cusum_ewma::detect_cusum" in lib and "cusum_ewma::compute_ewma_slope" in lib
    for bridge, literals in [("metric_filter", ("3.0, 0.5",)), ("trace_filter", ("95.0, 3",))]:
        text = (ROOT / f"rust/src/{bridge}/bridge.rs").read_text()
        for literal in literals:
            assert literal not in text, f"{bridge}/bridge.rs ainda fixa {literal}"
