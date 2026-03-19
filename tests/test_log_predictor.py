"""
Testes unitários para pipeline/log_predictor.py.
"""
from __future__ import annotations

import datetime

import pytest

from data.models import LogEntry
from pipeline.log_predictor import (
    LogBurstSignal,
    analyze_log_signals,
    compute_log_burst_rate,
    detect_novel_templates,
)

_TS = datetime.datetime(2026, 3, 4, 14, 0, 0)


def _make_entry(message: str, service: str = "svc", level: str = "INFO") -> LogEntry:
    """Cria uma LogEntry de teste com timestamp fixo."""
    return LogEntry(timestamp=_TS, level=level, service=service, message=message)


# ---------------------------------------------------------------------------
# compute_log_burst_rate
# ---------------------------------------------------------------------------


def test_no_burst_equal_windows():
    """Janelas com mesmo volume não devem gerar burst."""
    w1 = [_make_entry("msg_a")] * 10
    w2 = [_make_entry("msg_a")] * 10
    result = compute_log_burst_rate(w1, w2)
    assert result["burst_detected"] is False
    assert result["rate_ratio"] == pytest.approx(1.0)


def test_burst_triple_volume():
    """W2 com 3x o volume de W1 deve disparar burst."""
    w1 = [_make_entry("msg_a")] * 10
    w2 = [_make_entry("msg_b")] * 30
    result = compute_log_burst_rate(w1, w2)
    assert result["burst_detected"] is True
    assert result["rate_ratio"] == pytest.approx(3.0)
    assert result["dominant_template"] == "msg_b"


def test_burst_empty_window_1():
    """W1 vazia (baseline zero) com W2 não-vazia deve disparar burst."""
    w1: list[LogEntry] = []
    w2 = [_make_entry("msg_x")] * 5
    result = compute_log_burst_rate(w1, w2)
    assert result["burst_detected"] is True  # rate_ratio = 5 / 1 = 5
    assert result["dominant_template"] == "msg_x"


def test_no_burst_both_empty():
    """Ambas as janelas vazias não devem gerar burst."""
    result = compute_log_burst_rate([], [])
    assert result["burst_detected"] is False
    assert result["rate_ratio"] == pytest.approx(0.0)
    assert result["dominant_template"] is None


# ---------------------------------------------------------------------------
# detect_novel_templates
# ---------------------------------------------------------------------------


def test_novel_template_detection():
    """Template ausente no baseline deve ser retornado como novel."""
    known = {"template_old"}
    entries = [_make_entry("template_old"), _make_entry("template_new")]
    result = detect_novel_templates(known, entries)
    assert "template_new" in result["novel_templates"]
    assert "template_old" not in result["novel_templates"]


def test_no_novel_templates():
    """Todos os templates conhecidos não geram novidades."""
    known = {"msg_a", "msg_b"}
    entries = [_make_entry("msg_a"), _make_entry("msg_b")]
    result = detect_novel_templates(known, entries)
    assert result["novel_templates"] == []


def test_novel_templates_empty_inputs():
    """Entradas e conjunto conhecido vazios não devem lançar exceção."""
    result = detect_novel_templates(set(), [])
    assert result["novel_templates"] == []


# ---------------------------------------------------------------------------
# analyze_log_signals
# ---------------------------------------------------------------------------


def test_analyze_no_burst_uniform():
    """Distribuição uniforme não deve gerar burst."""
    entries = [_make_entry("msg_a")] * 20
    signal = analyze_log_signals(entries)
    assert isinstance(signal, LogBurstSignal)
    assert signal.burst_detected is False


def test_analyze_rate_ratio_uniform():
    """Lista uniforme resulta em rate_ratio ≈ 1.0 (split 50/50)."""
    # analyze_log_signals divide a lista ao meio, portanto ambas as janelas
    # têm o mesmo tamanho e rate_ratio ≈ 1.0.
    entries = [_make_entry("msg_a")] * 10
    signal = analyze_log_signals(entries)
    assert signal.rate_ratio == pytest.approx(1.0)
    assert signal.burst_detected is False


def test_analyze_novel_templates_in_second_half():
    """Templates novos na segunda metade devem aparecer em novel_templates."""
    w1_entries = [_make_entry("template_baseline")] * 5
    w2_entries = [_make_entry("template_new")] * 5
    entries = w1_entries + w2_entries
    signal = analyze_log_signals(entries)
    assert "template_new" in signal.novel_templates


def test_analyze_empty_list():
    """Lista vazia não deve lançar exceção."""
    signal = analyze_log_signals([])
    assert signal.burst_detected is False
    assert signal.novel_templates == []
