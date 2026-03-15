"""
Testes de benchmark para a extensão Rust _aiops_rust.

Valida targets de performance (Story 1.4):
  - LogParser (Rust): 100K logs em < 5s sem cap de amostragem
  - ContextCompressor (Rust): < 10ms para 1000 templates
  - Fallback Python: ambas as classes funcionam sem _aiops_rust
  - Concorrência: 4 threads chamando compress_context() sem deadlock
"""
from __future__ import annotations

import datetime
import random
import threading
import time

import pytest

# ── Constantes para geração de dados sintéticos ─────────────────────────────

_SERVICES = [
    "api-gateway", "payment-svc", "auth-svc",
    "postgres", "redis", "billing-svc",
]
_LEVELS = ["INFO", "INFO", "INFO", "WARN", "ERROR", "DEBUG"]
_MESSAGES = [
    "Connection pool exhausted: max_connections={n}",
    "Request timeout after {ms}ms on /checkout",
    "Database query failed: deadlock detected for user_{id}",
    "HTTP 500 Internal Server Error on /api/v2/order",
    "Circuit breaker opened for {svc}",
    "Cache miss for key user_session_{id}",
    "Request processed in {ms}ms by worker_{n}",
    "User {id} authenticated successfully via oauth",
    "Health check passed for service {svc}",
    "Metric collected: cpu_usage={pct}%",
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_log_entries(n: int, seed: int = 99):
    """Gera n objetos LogEntry sintéticos com seed determinístico."""
    from data.models import LogEntry

    rng = random.Random(seed)
    base_ts = datetime.datetime(2026, 3, 4, 14, 0, 0)
    logs = []
    for i in range(n):
        ts = base_ts + datetime.timedelta(seconds=i * 0.05)
        level = rng.choice(_LEVELS)
        service = rng.choice(_SERVICES)
        msg = rng.choice(_MESSAGES).format(
            n=rng.randint(1, 200),
            ms=rng.randint(1, 30000),
            id=rng.randint(1, 10000),
            svc=service,
            pct=rng.randint(10, 99),
        )
        logs.append(LogEntry(timestamp=ts, level=level, service=service, message=msg))
    return logs


def _make_aggregated_report(n_templates: int):
    """Gera AggregatedReport sintético com n_templates LogTemplate."""
    from pipeline.aggregator import AggregatedReport
    from pipeline.log_parser import LogTemplate

    rng = random.Random(99)
    templates = []
    for i in range(n_templates):
        tpl = LogTemplate(
            template=f"Connection pool exhausted max_connections={i % 50}",
            count=rng.randint(1, 200),
            level=rng.choice(["ERROR", "WARN", "INFO"]),
            relevance_score=rng.uniform(0.0, 10.0),
        )
        tpl.services = {rng.choice(_SERVICES)}
        templates.append(tpl)

    return AggregatedReport(
        log_templates=templates,
        metric_anomalies=[],
        slow_traces=[],
        total_logs=sum(t.count for t in templates),
        total_metrics=0,
        total_traces=0,
    )


# ── Benchmarks ───────────────────────────────────────────────────────────────

@pytest.mark.slow
def test_no_sampling_cap_100k():
    """100K logs parseados via Rust em < 5s sem descartar nenhum log."""
    from pipeline.log_parser import LogParser, _RUST_AVAILABLE

    if not _RUST_AVAILABLE:
        pytest.skip("_aiops_rust não disponível — benchmark Rust apenas")

    logs = _make_log_entries(100_000)

    parser = LogParser()
    t0 = time.perf_counter()
    templates = parser.parse(logs)
    elapsed = time.perf_counter() - t0

    assert elapsed < 5.0, (
        f"100K logs levaram {elapsed:.2f}s — limite é 5s"
    )
    assert len(templates) > 0, "Nenhum template retornado para 100K logs"

    # Sem cap de amostragem: a soma dos counts deve ser exatamente 100K
    total_processed = sum(t.count for t in templates)
    assert total_processed == 100_000, (
        f"Logs processados: {total_processed}, esperado 100_000 (sem sampling)"
    )


@pytest.mark.slow
def test_compressor_under_10ms():
    """compress_context Rust: 1000 templates comprimidos em < 10ms."""
    try:
        import _aiops_rust
    except ImportError:
        pytest.skip("_aiops_rust não disponível")

    report = _make_aggregated_report(n_templates=1000)

    # Warm-up: descarta latência de JIT/inicialização
    _aiops_rust.compress_context(report)

    t0 = time.perf_counter()
    text, stats_dict = _aiops_rust.compress_context(report)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert elapsed_ms < 10.0, (
        f"compress_context levou {elapsed_ms:.2f}ms — limite é 10ms"
    )
    assert isinstance(text, str) and len(text) > 0
    assert isinstance(stats_dict, dict)


@pytest.mark.unit
def test_fallback_mode(monkeypatch):
    """Sem _aiops_rust, LogParser e ContextCompressor fazem fallback sem erro."""
    import pipeline.log_parser as lp_mod
    import pipeline.context_compressor as cc_mod

    monkeypatch.setattr(lp_mod, "_RUST_AVAILABLE", False)
    monkeypatch.setattr(lp_mod, "_rust", None)
    monkeypatch.setattr(cc_mod, "_RUST_AVAILABLE", False)
    monkeypatch.setattr(cc_mod, "_rust", None)

    # LogParser fallback — funciona com sample pequeno
    from pipeline.log_parser import LogParser
    logs = _make_log_entries(50)
    parser = LogParser()
    templates = parser.parse(logs)
    # fallback processa sem exceção; número de templates pode variar
    assert isinstance(templates, list)

    # ContextCompressor fallback — comprime sem exceção
    from pipeline.context_compressor import ContextCompressor
    from pipeline.aggregator import AggregatedReport
    from pipeline.log_parser import LogTemplate

    tpl = LogTemplate(
        template="Connection refused to database",
        count=10,
        level="ERROR",
        relevance_score=8.0,
    )
    tpl.services = {"payment-svc"}
    report = AggregatedReport(
        log_templates=[tpl],
        metric_anomalies=[],
        slow_traces=[],
        total_logs=10,
        total_metrics=0,
        total_traces=0,
    )
    compressor = ContextCompressor()
    text, stats = compressor.compress(report)
    assert isinstance(text, str) and len(text) > 0


@pytest.mark.unit
def test_concurrent_compress():
    """4 threads chamam compress_context simultaneamente — sem deadlock."""
    try:
        import _aiops_rust
    except ImportError:
        pytest.skip("_aiops_rust não disponível")

    report = _make_aggregated_report(n_templates=100)
    results: list = []
    errors: list = []

    def _worker():
        try:
            result = _aiops_rust.compress_context(report)
            results.append(result)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=15.0)

    assert not errors, f"Exceções em threads: {errors}"
    assert len(results) == 4, (
        f"Apenas {len(results)}/4 threads completaram (possível deadlock)"
    )

    # Resultado determinístico entre threads
    texts = [r[0] for r in results]
    assert all(t == texts[0] for t in texts), (
        "compress_context não é determinístico entre threads concorrentes"
    )
