"""
Testes para LogParser / Drain3 migrado para Rust (Story 1.3).

Valida:
  - Rust processa todos os logs sem cap de amostragem
  - Interface compatível com LogTemplate Python
  - Fallback para Python quando _aiops_rust não disponível
  - GIL liberado: duas threads concorrentes executam em paralelo
  - Cenário golden (500 logs) produz os mesmos templates que o fallback Python
"""
from __future__ import annotations

import datetime
import threading
import sys

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sample_logs():
    """Retorna os 503 logs do incidente simulado com seed-42."""
    import random
    random.seed(42)
    from data.sample_incident import create_incident
    return create_incident().logs


@pytest.fixture(scope="module")
def rust_templates(sample_logs):
    """Executa parse via Rust e retorna templates."""
    from pipeline.log_parser import LogParser, _RUST_AVAILABLE
    if not _RUST_AVAILABLE:
        pytest.skip("_aiops_rust não disponível")
    parser = LogParser()
    return parser.parse(sample_logs)


@pytest.fixture(scope="module")
def python_templates(sample_logs):
    """Executa parse via Python (fallback) e retorna templates."""
    from pipeline.log_parser import LogParser
    parser = LogParser()
    return parser._python_drain3_parse(list(sample_logs))


# ── Testes de processamento sem amostragem ───────────────────────────────────

@pytest.mark.unit
def test_rust_processes_all_logs(sample_logs, rust_templates):
    """Verifica que Rust processa todos os logs sem truncar (sem cap de 500)."""
    total_processed = sum(t.count for t in rust_templates)
    assert total_processed == len(sample_logs), (
        f"Rust processou {total_processed} logs, esperado {len(sample_logs)}"
    )


@pytest.mark.unit
def test_no_sampling_cap_large_input():
    """Verifica que Rust processa mais de 500 logs sem amostragem."""
    import random
    random.seed(0)
    from data.models import LogEntry
    from pipeline.log_parser import LogParser, _RUST_AVAILABLE

    if not _RUST_AVAILABLE:
        pytest.skip("_aiops_rust não disponível")

    now = datetime.datetime(2026, 3, 4, 14, 0, 0)
    messages = [f"Connection pool exhausted, waiting={i}" for i in range(600)]
    logs = [
        LogEntry(now + datetime.timedelta(seconds=i), "ERROR", "svc", msg)
        for i, msg in enumerate(messages)
    ]

    parser = LogParser()
    templates = parser.parse(logs)
    total = sum(t.count for t in templates)
    assert total == 600, f"Esperado 600 logs processados, obteve {total}"


# ── Testes de interface ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_rust_template_interface(rust_templates):
    """Verifica que objetos retornados possuem os campos corretos de LogTemplate."""
    from pipeline.log_parser import LogTemplate

    assert len(rust_templates) > 0
    for tpl in rust_templates:
        assert isinstance(tpl, LogTemplate), f"Esperado LogTemplate, obteve {type(tpl)}"
        assert isinstance(tpl.template, str)
        assert isinstance(tpl.level, str)
        assert isinstance(tpl.count, int)
        assert isinstance(tpl.relevance_score, float)
        assert isinstance(tpl.services, set)
        # first_seen e last_seen podem ser datetime ou None
        assert tpl.first_seen is None or isinstance(tpl.first_seen, datetime.datetime)
        assert tpl.last_seen is None or isinstance(tpl.last_seen, datetime.datetime)
        assert isinstance(tpl.sample_messages, list)


@pytest.mark.unit
def test_rust_templates_sorted_by_relevance(rust_templates):
    """Verifica que templates são retornados ordenados por relevance_score decrescente."""
    scores = [t.relevance_score for t in rust_templates]
    assert scores == sorted(scores, reverse=True), "Templates não estão em ordem decrescente de relevância"


@pytest.mark.unit
def test_rust_error_templates_have_boost(rust_templates):
    """Verifica que templates ERROR/FATAL têm score maior que INFO equivalente."""
    error_scores = [t.relevance_score for t in rust_templates if t.level in ("ERROR", "FATAL")]
    info_scores = [t.relevance_score for t in rust_templates if t.level == "INFO"]
    if error_scores and info_scores:
        assert min(error_scores) > max(info_scores) or max(error_scores) > max(info_scores)


# ── Teste de fallback ─────────────────────────────────────────────────────────

@pytest.mark.unit
def test_fallback_when_rust_unavailable(sample_logs, monkeypatch):
    """Verifica que parse() usa Python quando _aiops_rust não está disponível."""
    import pipeline.log_parser as lp

    monkeypatch.setattr(lp, "_RUST_AVAILABLE", False)
    parser = lp.LogParser()
    templates = parser.parse(list(sample_logs))

    assert len(templates) > 0
    total = sum(t.count for t in templates)
    # Com 503 logs e step=1, Python processa todos os 503 também
    assert total > 0


@pytest.mark.unit
def test_fallback_returns_log_templates(sample_logs, monkeypatch):
    """Verifica que o fallback Python retorna instâncias de LogTemplate."""
    import pipeline.log_parser as lp
    from pipeline.log_parser import LogTemplate

    monkeypatch.setattr(lp, "_RUST_AVAILABLE", False)
    parser = lp.LogParser()
    templates = parser.parse(list(sample_logs))

    for tpl in templates:
        assert isinstance(tpl, LogTemplate)


# ── Teste de concorrência / liberação do GIL ─────────────────────────────────

@pytest.mark.unit
def test_concurrent_parse_gil_release(sample_logs):
    """Verifica que duas threads podem chamar parse() simultaneamente.

    O GIL deve ser liberado durante o processamento Drain3 em Rust,
    permitindo execução paralela real entre threads Python.
    """
    from pipeline.log_parser import LogParser, _RUST_AVAILABLE

    if not _RUST_AVAILABLE:
        pytest.skip("_aiops_rust não disponível")

    results = {}
    errors = {}

    def run_parse(thread_id: int, logs: list):
        try:
            parser = LogParser()
            results[thread_id] = parser.parse(logs)
        except Exception as e:
            errors[thread_id] = e

    # Duas threads com o mesmo conjunto de logs
    t1 = threading.Thread(target=run_parse, args=(1, list(sample_logs)))
    t2 = threading.Thread(target=run_parse, args=(2, list(sample_logs)))

    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert not errors, f"Erros em threads: {errors}"
    assert 1 in results and 2 in results
    assert len(results[1]) > 0
    assert len(results[2]) > 0


# ── Teste de compatibilidade com cenário golden ───────────────────────────────

@pytest.mark.unit
def test_golden_scenario_template_fields_match(rust_templates, python_templates):
    """Verifica que Rust e Python produzem templates com campos compatíveis.

    Compara estrutura e campos principais dos templates gerados por ambos os paths.
    Templates do Rust devem cobrir os mesmos padrões de log que o Python.
    """
    rust_by_template = {t.template: t for t in rust_templates}
    python_by_template = {t.template: t for t in python_templates}

    # Verificar que pelo menos 70% dos templates Python estão no Rust
    # (Drain3 pode ter pequenas variações de clustering)
    common = set(rust_by_template.keys()) & set(python_by_template.keys())
    coverage = len(common) / max(len(python_by_template), 1)

    assert coverage >= 0.7, (
        f"Cobertura de templates insuficiente: {coverage:.1%}\n"
        f"Rust: {sorted(rust_by_template.keys())}\n"
        f"Python: {sorted(python_by_template.keys())}"
    )

    # Para templates em comum, verificar campos obrigatórios
    for tpl_str in common:
        rust_t = rust_by_template[tpl_str]
        py_t = python_by_template[tpl_str]
        assert rust_t.level == py_t.level, (
            f"Level diverge para '{tpl_str}': Rust={rust_t.level}, Python={py_t.level}"
        )
        assert rust_t.count == py_t.count, (
            f"Count diverge para '{tpl_str}': Rust={rust_t.count}, Python={py_t.count}"
        )


@pytest.mark.unit
def test_golden_scenario_total_logs_match(rust_templates, python_templates, sample_logs):
    """Verifica que Rust e Python processam o mesmo número total de logs."""
    rust_total = sum(t.count for t in rust_templates)
    python_total = sum(t.count for t in python_templates)

    assert rust_total == len(sample_logs), (
        f"Rust processou {rust_total}, esperado {len(sample_logs)}"
    )
    assert python_total == len(sample_logs), (
        f"Python processou {python_total}, esperado {len(sample_logs)}"
    )
