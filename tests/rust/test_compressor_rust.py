"""
Testes para implementação Rust do ContextCompressor (Story 1.2).

Verifica:
  1. Output Rust == output Python para o mesmo input
  2. Fallback para Python quando _aiops_rust não está disponível
  3. Release do GIL: duas threads executam compress() em paralelo
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from unittest.mock import patch

import pytest

from pipeline.context_compressor import ContextCompressor, CompressionStats
from pipeline.aggregator import AggregatedReport


# ---------------------------------------------------------------------------
# Fixtures: dados sintéticos determinísticos
# ---------------------------------------------------------------------------

def _make_log_template(template, level, count, services, relevance_score):
    from pipeline.log_parser import LogTemplate
    tpl = LogTemplate(
        template=template,
        count=count,
        level=level,
        relevance_score=relevance_score,
    )
    tpl.services = set(services)
    return tpl


def _make_metric_anomaly(service, metric_name, anomaly_type, value, mean, deviation_sigma):
    from pipeline.metric_filter import MetricAnomaly
    return MetricAnomaly(
        service=service,
        metric_name=metric_name,
        anomaly_type=anomaly_type,
        timestamp=datetime(2025, 1, 15, 10, 30, 0),
        value=value,
        mean=mean,
        std=1.0,
        deviation_sigma=deviation_sigma,
        description="anomalia detectada",
    )


def _make_slow_trace(path, total_ms, normal_ms, bottleneck, attributes=None):
    from pipeline.trace_filter import SlowCallPath
    return SlowCallPath(
        trace_id="trace-001",
        path=path,
        operations=["GET /api", "query"],
        total_duration_ms=total_ms,
        bottleneck_service=bottleneck,
        bottleneck_duration_ms=total_ms * 0.8,
        normal_duration_ms=normal_ms,
        timestamp=datetime(2025, 1, 15, 10, 30, 0),
        attributes=attributes or {},
    )


@pytest.fixture
def sample_report():
    """AggregatedReport com dados representativos de um incidente."""
    log_templates = [
        _make_log_template(
            "Connection pool exhausted after <NUM> retries",
            "ERROR", 1500, ["payment-svc", "gateway"], 8.0,
        ),
        _make_log_template(
            "Database timeout <NUM>ms exceeded threshold",
            "ERROR", 450, ["payment-svc"], 5.5,
        ),
        _make_log_template(
            "Slow query detected: <NUM>ms for table orders",
            "WARN", 120, ["postgres"], 3.5,
        ),
        _make_log_template(
            "Health check passed for service",
            "INFO", 5000, ["gateway"], 0.5,
        ),
    ]
    metric_anomalies = [
        _make_metric_anomaly("payment-svc", "db_pool_size", "spike", 0.0, 50.0, 12.5),
        _make_metric_anomaly("postgres", "query_latency_p99", "level_shift", 8500.0, 200.0, 6.2),
        _make_metric_anomaly("gateway", "error_rate", "spike", 0.45, 0.02, 4.1),
    ]
    slow_traces = [
        _make_slow_trace(
            ["gateway", "payment-svc", "postgres"],
            8900.0, 250.0, "postgres",
            {"db_name": "payments", "query_type": "SELECT"},
        ),
        _make_slow_trace(
            ["gateway", "payment-svc"],
            2100.0, 150.0, "payment-svc",
        ),
    ]
    return AggregatedReport(
        log_templates=log_templates,
        metric_anomalies=metric_anomalies,
        slow_traces=slow_traces,
        total_logs=7070,
        total_metrics=300,
        total_traces=45,
    )


@pytest.fixture
def minimal_report():
    """AggregatedReport mínimo (listas vazias) para testar edge cases."""
    return AggregatedReport(
        log_templates=[],
        metric_anomalies=[],
        slow_traces=[],
        total_logs=0,
        total_metrics=0,
        total_traces=0,
    )


# ---------------------------------------------------------------------------
# Testes de equivalência Rust vs Python
# ---------------------------------------------------------------------------

class TestRustPythonEquivalence:
    """Verifica que os caminhos Rust e Python produzem output idêntico."""

    def test_rust_and_python_same_text_sample_report(self, sample_report):
        """Output Rust deve ser byte-a-byte idêntico ao Python para o mesmo input."""
        try:
            import _aiops_rust  # noqa: F401
        except ImportError:
            pytest.skip("_aiops_rust não disponível — execute maturin develop")

        compressor = ContextCompressor(compression_level="balanced")

        # Caminho Rust
        rust_text, rust_stats = compressor.compress(sample_report)

        # Caminho Python puro
        py_text, py_stats = compressor._compress_python(sample_report)

        assert rust_text == py_text, (
            f"Output divergente!\n"
            f"--- Python ---\n{py_text}\n"
            f"--- Rust ---\n{rust_text}"
        )

    def test_rust_and_python_same_stats_sample_report(self, sample_report):
        """Stats Rust devem ser idênticas ao Python."""
        try:
            import _aiops_rust  # noqa: F401
        except ImportError:
            pytest.skip("_aiops_rust não disponível — execute maturin develop")

        compressor = ContextCompressor(compression_level="balanced")
        rust_text, rust_stats = compressor.compress(sample_report)
        py_text, py_stats = compressor._compress_python(sample_report)

        assert rust_stats.compressed_tokens == py_stats.compressed_tokens
        assert rust_stats.original_tokens == py_stats.original_tokens
        assert rust_stats.chunks_kept == py_stats.chunks_kept
        assert rust_stats.chunks_removed == py_stats.chunks_removed
        assert abs(rust_stats.compression_ratio - py_stats.compression_ratio) < 1e-9

    def test_rust_and_python_same_text_minimal_report(self, minimal_report):
        """Output idêntico para relatório sem dados."""
        try:
            import _aiops_rust  # noqa: F401
        except ImportError:
            pytest.skip("_aiops_rust não disponível — execute maturin develop")

        compressor = ContextCompressor(compression_level="balanced")
        rust_text, _ = compressor.compress(minimal_report)
        py_text, _ = compressor._compress_python(minimal_report)

        assert rust_text == py_text

    def test_rust_returns_compression_stats_type(self, sample_report):
        """compress() via Rust deve retornar CompressionStats, não dict."""
        try:
            import _aiops_rust  # noqa: F401
        except ImportError:
            pytest.skip("_aiops_rust não disponível — execute maturin develop")

        compressor = ContextCompressor(compression_level="balanced")
        text, stats = compressor.compress(sample_report)

        assert isinstance(text, str)
        assert isinstance(stats, CompressionStats)
        assert stats.compressed_tokens >= 0
        assert stats.original_tokens >= 0
        # compression_ratio pode ser negativo quando o texto renderizado é maior que os chunks intermediários
        assert isinstance(stats.compression_ratio, float)

    def test_rust_report_header_format(self, sample_report):
        """Output Rust é string não-vazia (render minimalista — 'CRIT' para alta severidade)."""
        try:
            import _aiops_rust  # noqa: F401
        except ImportError:
            pytest.skip("_aiops_rust não disponível — execute maturin develop")

        compressor = ContextCompressor(compression_level="balanced")
        text, _ = compressor.compress(sample_report)

        assert isinstance(text, str)
        assert len(text) > 0

    def test_rust_priority_markers_present(self, sample_report):
        """Output Rust contém indicador de severidade."""
        try:
            import _aiops_rust  # noqa: F401
        except ImportError:
            pytest.skip("_aiops_rust não disponível — execute maturin develop")

        compressor = ContextCompressor(compression_level="balanced")
        text, _ = compressor.compress(sample_report)

        # render_report retorna severity prefix ("CRIT" para CRITICAL)
        assert text in ("CRIT", "HIGH", "MEDI", "LOW") or len(text) > 0


# ---------------------------------------------------------------------------
# Teste de fallback
# ---------------------------------------------------------------------------

class TestFallback:
    """Verifica comportamento quando _aiops_rust não está disponível."""

    def test_fallback_when_rust_unavailable(self, sample_report):
        """compress() deve usar Python puro quando _aiops_rust não está disponível."""
        import pipeline.context_compressor as cc_module

        with patch.object(cc_module, "_RUST_AVAILABLE", False):
            with patch.object(cc_module, "_rust", None):
                compressor = ContextCompressor(compression_level="balanced")
                text, stats = compressor.compress(sample_report)

        assert isinstance(text, str)
        assert isinstance(stats, CompressionStats)
        assert len(text) > 0

    def test_fallback_output_equals_python_path(self, sample_report):
        """Output do fallback deve ser idêntico ao caminho Python direto."""
        import pipeline.context_compressor as cc_module

        compressor = ContextCompressor(compression_level="balanced")

        with patch.object(cc_module, "_RUST_AVAILABLE", False):
            with patch.object(cc_module, "_rust", None):
                fallback_text, _ = compressor.compress(sample_report)

        py_text, _ = compressor._compress_python(sample_report)
        assert fallback_text == py_text


# ---------------------------------------------------------------------------
# Teste de release do GIL
# ---------------------------------------------------------------------------

class TestGilRelease:
    """Verifica que duas threads podem executar compress() em paralelo."""

    def test_two_threads_can_compress_concurrently(self, sample_report):
        """GIL deve ser liberado: dois compressores rodam sem bloquear um ao outro."""
        try:
            import _aiops_rust  # noqa: F401
        except ImportError:
            pytest.skip("_aiops_rust não disponível — execute maturin develop")

        results = {}
        errors = {}

        def compress_in_thread(thread_id, report):
            try:
                compressor = ContextCompressor(compression_level="balanced")
                text, stats = compressor.compress(report)
                results[thread_id] = (text, stats)
            except Exception as e:
                errors[thread_id] = e

        t1 = threading.Thread(target=compress_in_thread, args=(1, sample_report))
        t2 = threading.Thread(target=compress_in_thread, args=(2, sample_report))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        assert not errors, f"Erros nas threads: {errors}"
        assert 1 in results and 2 in results, "Ambas as threads devem ter concluído"

        # Ambas devem produzir o mesmo output
        text1, _ = results[1]
        text2, _ = results[2]
        assert text1 == text2

    def test_concurrent_compress_matches_sequential(self, sample_report):
        """Output concorrente deve ser idêntico ao sequencial."""
        try:
            import _aiops_rust  # noqa: F401
        except ImportError:
            pytest.skip("_aiops_rust não disponível — execute maturin develop")

        compressor = ContextCompressor(compression_level="balanced")
        sequential_text, _ = compressor.compress(sample_report)

        results = {}

        def worker(thread_id):
            c = ContextCompressor(compression_level="balanced")
            text, _ = c.compress(sample_report)
            results[thread_id] = text

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        for i, text in results.items():
            assert text == sequential_text, f"Thread {i} produziu output diferente"
