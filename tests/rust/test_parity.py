"""F6-1: paridade Rust ↔ Python nos estágios acelerados.

Antes desta fase:
- MetricFilter/TraceFilter só delegavam ao Rust com os thresholds DEFAULT;
  qualquer config custom caía no Python sem aviso, e os dois paths divergiam.
- TelemetrySanitizer com ``custom_patterns`` usava o Rust, que não conhece os
  padrões extras — payload casado só por padrão custom passava como CLEAN.
- LogParser Python amostrava ≤500 logs (o cenário default tem 503) e acumulava
  templates entre chamadas; o Rust fazia nem um nem outro.

Estes testes rodam os dois paths com o MESMO input e exigem o MESMO output
(ou diferenças explicitamente justificadas). Requerem ``_aiops_rust``.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

pytest.importorskip("_aiops_rust")

import pipeline.log_parser as lp  # noqa: E402
import pipeline.metric_filter as mf  # noqa: E402
import pipeline.telemetry_sanitizer as ts  # noqa: E402
import pipeline.trace_filter as tf  # noqa: E402
from data.models import LogEntry, MetricPoint, Span  # noqa: E402


@pytest.fixture(scope="module")
def incident():
    random.seed(42)
    from data.sample_incident import create_incident

    return create_incident()


def _no_rust(monkeypatch, module):
    monkeypatch.setattr(module, "_RUST_AVAILABLE", False)
    monkeypatch.setattr(module, "_rust", None)


def _anomaly_key(a: mf.MetricAnomaly) -> tuple:
    return (
        a.service,
        a.metric_name,
        a.anomaly_type,
        int(a.timestamp.timestamp()),
        a.value,
        a.mean,
        a.std,
        a.deviation_sigma,
    )


# ── MetricFilter ─────────────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "sigma, ratio",
    [(3.0, 0.5), (2.0, 0.5), (4.0, 0.3), (2.5, 0.6)],
    ids=["default", "sigma2", "sigma4-ratio0.3", "sigma2.5-ratio0.6"],
)
def test_metric_filter_rust_matches_python_for_any_threshold(
    incident, monkeypatch, sigma, ratio
):
    rust_out = mf.MetricFilter(sigma, ratio).detect_anomalies(incident.metrics)

    _no_rust(monkeypatch, mf)
    py_out = mf.MetricFilter(sigma, ratio).detect_anomalies(incident.metrics)

    assert sorted(map(_anomaly_key, rust_out)) == sorted(map(_anomaly_key, py_out))
    # Thresholds mais frouxos acham >= anomalias que os estritos (sanidade)
    assert len(rust_out) == len(py_out)


@pytest.mark.unit
def test_metric_filter_custom_threshold_actually_reaches_rust(incident, monkeypatch):
    """Config custom não cai mais no fallback silencioso."""
    calls: list[dict] = []
    real = mf._rust.filter_metrics

    def spy(metrics, **kw):
        calls.append(kw)
        return real(metrics, **kw)

    monkeypatch.setattr(mf._rust, "filter_metrics", spy)
    mf.MetricFilter(sigma_threshold=2.0, baseline_ratio=0.4).detect_anomalies(
        incident.metrics
    )
    assert calls == [{"sigma_threshold": 2.0, "baseline_ratio": 0.4}]


@pytest.mark.unit
def test_metric_filter_rust_rejects_invalid_params():
    import _aiops_rust

    with pytest.raises(ValueError):
        _aiops_rust.filter_metrics([], sigma_threshold=0.0)
    with pytest.raises(ValueError):
        _aiops_rust.filter_metrics([], baseline_ratio=1.0)


# ── TraceFilter ──────────────────────────────────────────────────────────────


def _path_key(p: tf.SlowCallPath) -> tuple:
    return (
        p.trace_id,
        tuple(p.path),
        tuple(p.operations),
        round(p.total_duration_ms, 3),
        p.bottleneck_service,
        round(p.bottleneck_duration_ms, 3),
        round(p.normal_duration_ms, 1),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "pct, hops",
    [(95.0, 3), (90.0, 3), (99.0, 2), (80.0, 5)],
    ids=["default", "p90", "p99-2hops", "p80-5hops"],
)
def test_trace_filter_rust_matches_python_for_any_threshold(
    incident, monkeypatch, pct, hops
):
    rust_out = tf.TraceFilter(pct, hops).filter_slow_traces(incident.traces)

    _no_rust(monkeypatch, tf)
    py_out = tf.TraceFilter(pct, hops).filter_slow_traces(incident.traces)

    assert sorted(map(_path_key, rust_out)) == sorted(map(_path_key, py_out))


@pytest.mark.unit
def test_trace_filter_custom_threshold_actually_reaches_rust(incident, monkeypatch):
    calls: list[dict] = []
    real = tf._rust.filter_traces

    def spy(spans, **kw):
        calls.append(kw)
        return real(spans, **kw)

    monkeypatch.setattr(tf._rust, "filter_traces", spy)
    tf.TraceFilter(percentile_threshold=90.0, max_hops=4).filter_slow_traces(
        incident.traces
    )
    assert calls == [{"percentile_threshold": 90.0, "max_hops": 4}]


@pytest.mark.unit
def test_trace_filter_rust_rejects_invalid_params():
    import _aiops_rust

    with pytest.raises(ValueError):
        _aiops_rust.filter_traces([], percentile_threshold=101.0)
    with pytest.raises(ValueError):
        _aiops_rust.filter_traces([], max_hops=0)


# ── TelemetrySanitizer ───────────────────────────────────────────────────────

_TAINT_SAMPLES = [
    "Connection pool exhausted, 80 requests waiting",
    "404s are caused by nginx not supporting SSL; add PPA ppa:ngx/latest",
    "[SOLUTION] downgrade to openssl 1.0.2",
    "GET /jedijwjd29fjce0.php 404",
    "request user-agent=Mozilla/5.0 referer=http://x",
    "x" * 120,
    "",
]


@pytest.mark.unit
@pytest.mark.parametrize("text", _TAINT_SAMPLES)
def test_sanitizer_base_patterns_rust_matches_python(monkeypatch, text):
    rust_level = ts.TelemetrySanitizer()._detect_taint(text)
    _no_rust(monkeypatch, ts)
    py_level = ts.TelemetrySanitizer()._detect_taint(text)
    assert rust_level == py_level


@pytest.mark.unit
def test_sanitizer_custom_patterns_are_honoured_not_ignored(monkeypatch):
    """Payload casado só por padrão custom era CLEAN no path Rust."""
    sanitizer = ts.TelemetrySanitizer(custom_patterns=[r"magic-token-\d+"])
    assert sanitizer._detect_taint("see magic-token-42") == ts.TaintLevel.ADVERSARIAL

    # batch também
    now = datetime.now()
    logs = [
        LogEntry(timestamp=now, level="INFO", service="svc", message="magic-token-7")
    ]
    sanitized, report = sanitizer.sanitize_logs(logs)
    assert report.adversarial_entries == 1
    assert "magic-token-7" not in sanitized[0].message


@pytest.mark.unit
def test_sanitizer_custom_patterns_warn_once_and_skip_rust(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ts._rust, "detect_taint_single", lambda t: calls.append(t) or "clean"
    )
    warnings = []
    monkeypatch.setattr(
        ts.log, "warning", lambda event, **kw: warnings.append((event, kw))
    )

    sanitizer = ts.TelemetrySanitizer(custom_patterns=[r"foo"])
    sanitizer._detect_taint("a")
    sanitizer._detect_taint("b")

    assert calls == []  # Rust não foi consultado
    assert [w[0] for w in warnings] == ["sanitizer.rust_fallback_custom_patterns"]
    assert warnings[0][1]["custom_patterns"] == 1


@pytest.mark.unit
def test_sanitizer_without_custom_patterns_uses_rust(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ts._rust, "detect_taint_single", lambda t: calls.append(t) or "clean"
    )
    ts.TelemetrySanitizer()._detect_taint("hello")
    assert calls == ["hello"]


# ── LogParser ────────────────────────────────────────────────────────────────


@pytest.mark.unit
def test_log_parser_python_fallback_processes_all_logs(incident, monkeypatch):
    """O fallback amostrava ≤500; o cenário default tem 503 → perdia 3."""
    assert len(incident.logs) > 500
    _no_rust(monkeypatch, lp)
    templates = lp.LogParser().parse(list(incident.logs))
    assert sum(t.count for t in templates) == len(incident.logs)


@pytest.mark.unit
def test_log_parser_rust_and_python_count_the_same_logs(incident, monkeypatch):
    rust_total = sum(t.count for t in lp.LogParser().parse(list(incident.logs)))
    _no_rust(monkeypatch, lp)
    py_total = sum(t.count for t in lp.LogParser().parse(list(incident.logs)))
    assert rust_total == py_total == len(incident.logs)


@pytest.mark.unit
def test_log_parser_is_stateless_between_calls_in_both_paths(monkeypatch):
    now = datetime.now()
    logs = [
        LogEntry(
            timestamp=now + timedelta(seconds=i),
            level="ERROR",
            service="s",
            message="boom 1",
        )
        for i in range(4)
    ]

    rust_parser = lp.LogParser()
    rust_parser.parse(logs)
    second_rust = rust_parser.parse(logs)
    assert sum(t.count for t in second_rust) == 4  # não 8

    _no_rust(monkeypatch, lp)
    py_parser = lp.LogParser()
    py_parser.parse(logs)
    second_py = py_parser.parse(logs)
    assert sum(t.count for t in second_py) == 4  # Python acumulava: dava 8


@pytest.mark.unit
def test_log_parser_get_error_templates_works_in_both_paths(monkeypatch):
    now = datetime.now()
    logs = [
        LogEntry(timestamp=now, level="ERROR", service="s", message="db timeout"),
        LogEntry(timestamp=now, level="INFO", service="s", message="ok"),
    ]
    rust_parser = lp.LogParser()
    rust_parser.parse(logs)
    assert [t.level for t in rust_parser.get_error_templates()] == ["ERROR"]

    _no_rust(monkeypatch, lp)
    py_parser = lp.LogParser()
    py_parser.parse(logs)
    assert [t.level for t in py_parser.get_error_templates()] == ["ERROR"]


@pytest.mark.unit
def test_log_parser_to_text_without_args_uses_last_parse(monkeypatch):
    now = datetime.now()
    logs = [LogEntry(timestamp=now, level="ERROR", service="svc-a", message="oops")]
    parser = lp.LogParser()
    parser.parse(logs)
    text = parser.to_text()
    assert "oops" in text and "svc-a" in text


# ── Smoke: Span/MetricPoint com thresholds custom não explodem na ponte ──────


@pytest.mark.unit
def test_bridges_accept_domain_objects_with_custom_params():
    now = datetime.now()
    metrics = [
        MetricPoint(
            timestamp=now + timedelta(seconds=i),
            service="s",
            metric_name="m",
            value=float(i),
        )
        for i in range(20)
    ]
    spans = [
        Span(
            trace_id="t1",
            span_id="a",
            parent_span_id=None,
            service="gw",
            operation="op",
            start_time=now,
            duration_ms=100.0,
            status="ok",
            attributes={},
        )
    ]
    assert isinstance(mf.MetricFilter(2.0, 0.4).detect_anomalies(metrics), list)
    assert isinstance(tf.TraceFilter(50.0, 1).filter_slow_traces(spans), list)
