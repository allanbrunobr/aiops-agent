"""
Testes de validação da bridge PyO3 (_aiops_rust).

Valida (Story 1.4):
  - Import de _aiops_rust bem-sucedido
  - compress_context e parse_logs são callables com assinaturas corretas
  - Tipos de retorno corretos: tuple(str, dict) e list[dict]
  - Inputs inválidos levantam PyException, não panic/crash do processo
"""
from __future__ import annotations

import datetime

import pytest


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def rust():
    """Importa _aiops_rust ou pula o módulo se não disponível."""
    try:
        import _aiops_rust as m
        return m
    except ImportError:
        pytest.skip("_aiops_rust não disponível")


# ── Import ────────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_import_succeeds():
    """Importação de _aiops_rust não levanta exceção."""
    import _aiops_rust  # noqa: F401


@pytest.mark.unit
def test_compress_context_callable(rust):
    """_aiops_rust.compress_context é callable."""
    assert callable(rust.compress_context)


@pytest.mark.unit
def test_parse_logs_callable(rust):
    """_aiops_rust.parse_logs é callable."""
    assert callable(rust.parse_logs)


# ── compress_context ──────────────────────────────────────────────────────────

@pytest.mark.unit
def test_compress_context_return_type_empty(rust):
    """compress_context(report_vazio) retorna (str, dict) com chaves esperadas."""
    from pipeline.aggregator import AggregatedReport

    report = AggregatedReport(
        log_templates=[],
        metric_anomalies=[],
        slow_traces=[],
        total_logs=0,
        total_metrics=0,
        total_traces=0,
    )
    result = rust.compress_context(report)

    assert isinstance(result, tuple), f"Esperado tuple, obtido {type(result)}"
    assert len(result) == 2, f"Esperado 2 elementos, obtido {len(result)}"

    text, stats = result
    assert isinstance(text, str), f"Esperado str, obtido {type(text)}"
    assert isinstance(stats, dict), f"Esperado dict, obtido {type(stats)}"

    required_keys = {
        "original_tokens", "compressed_tokens",
        "chunks_kept", "chunks_removed",
        "critical_preserved", "compression_ratio",
    }
    missing = required_keys - set(stats.keys())
    assert not missing, f"Chaves faltando nos stats: {missing}"


@pytest.mark.unit
def test_compress_context_stats_valid_ranges(rust):
    """Valores numéricos nos stats de compress_context estão em intervalos válidos."""
    from pipeline.aggregator import AggregatedReport
    from pipeline.log_parser import LogTemplate

    tpl = LogTemplate(
        template="Database connection failed",
        count=100,
        level="ERROR",
        relevance_score=8.0,
    )
    tpl.services = {"payment-svc"}

    report = AggregatedReport(
        log_templates=[tpl],
        metric_anomalies=[],
        slow_traces=[],
        total_logs=100,
        total_metrics=0,
        total_traces=0,
    )
    _, stats = rust.compress_context(report)

    assert stats["original_tokens"] >= 0
    assert stats["compressed_tokens"] >= 0
    assert stats["chunks_kept"] >= 0
    assert stats["chunks_removed"] >= 0
    assert 0.0 <= stats["critical_preserved"] <= 100.0
    # compression_ratio pode ser negativo quando o output com cabeçalhos é maior que o input
    assert stats["compression_ratio"] <= 1.0


@pytest.mark.unit
def test_compress_context_invalid_input_raises(rust):
    """compress_context(None) levanta exceção Python — processo não trava."""
    with pytest.raises(Exception):
        rust.compress_context(None)


# ── parse_logs ────────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_parse_logs_return_type(rust):
    """parse_logs retorna list[dict] com chaves corretas para 1 log."""
    from data.models import LogEntry

    logs = [
        LogEntry(
            timestamp=datetime.datetime(2026, 3, 4, 14, 0, 0),
            level="ERROR",
            service="payment-svc",
            message="Connection refused to database",
        )
    ]
    result = rust.parse_logs(logs)

    assert isinstance(result, list), f"Esperado list, obtido {type(result)}"
    assert len(result) > 0, "parse_logs retornou lista vazia para 1 log"

    tpl = result[0]
    assert isinstance(tpl, dict), f"Item da lista esperado dict, obtido {type(tpl)}"

    required_keys = {"template", "level", "count", "relevance_score", "services"}
    missing = required_keys - set(tpl.keys())
    assert not missing, f"Chaves faltando no template dict: {missing}"


@pytest.mark.unit
def test_parse_logs_count_matches_input(rust):
    """A soma dos counts dos templates iguala o número de logs de entrada."""
    from data.models import LogEntry

    logs = [
        LogEntry(
            timestamp=datetime.datetime(2026, 3, 4, 14, 0, i),
            level="ERROR",
            service="api-gateway",
            message=f"Timeout on request {i}",
        )
        for i in range(10)
    ]
    result = rust.parse_logs(logs)
    total = sum(d["count"] for d in result)
    assert total == len(logs), (
        f"Sum dos counts ({total}) != len(logs) ({len(logs)})"
    )


@pytest.mark.unit
def test_parse_logs_invalid_input_raises(rust):
    """parse_logs(None) levanta exceção Python — processo não trava."""
    with pytest.raises(Exception):
        rust.parse_logs(None)
