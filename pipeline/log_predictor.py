"""
Predição de surtos e novidades em logs para o pipeline AIOps.

Detecta aumentos de taxa de log (burst) e templates novos entre janelas
temporais adjacentes, emitindo sinais de alerta antecipado.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from data.models import LogEntry


@dataclass
class LogBurstSignal:
    """Sinal de surto e novidade detectado no fluxo de logs."""

    burst_detected: bool
    rate_ratio: float
    dominant_template: str | None
    novel_templates: list[str] = field(default_factory=list)


def _get_template_id(entry: LogEntry) -> str:
    """Retorna o identificador de template de uma entrada de log.

    Como LogEntry não possui campo template_id, utiliza a mensagem
    como chave de template.
    """
    return entry.message


def compute_log_burst_rate(
    window_1: list[LogEntry],
    window_2: list[LogEntry],
) -> dict:
    """Computa a taxa de surto entre duas janelas temporais de logs.

    Parâmetros
    ----------
    window_1 : list[LogEntry]
        Janela de baseline (passado).
    window_2 : list[LogEntry]
        Janela corrente (presente).

    Retorna
    -------
    dict com chaves:
        burst_detected (bool): True se rate_ratio >= 2.0
        rate_ratio (float): proporção len(window_2) / max(len(window_1), 1)
        dominant_template (str | None): template mais frequente em window_2
    """
    rate_ratio = len(window_2) / max(len(window_1), 1)
    burst_detected = rate_ratio >= 2.0

    dominant_template: str | None = None
    if window_2:
        counts = Counter(_get_template_id(e) for e in window_2)
        dominant_template = counts.most_common(1)[0][0]

    return {
        "burst_detected": burst_detected,
        "rate_ratio": rate_ratio,
        "dominant_template": dominant_template,
    }


def detect_novel_templates(
    known_ids: set[str],
    new_entries: list[LogEntry],
) -> dict:
    """Detecta templates nunca vistos antes no conjunto de ids conhecidos.

    Parâmetros
    ----------
    known_ids : set[str]
        Conjunto de IDs de templates conhecidos (baseline).
    new_entries : list[LogEntry]
        Entradas da janela corrente a inspecionar.

    Retorna
    -------
    dict com chave:
        novel_templates (list[str]): lista de IDs inéditos encontrados.
    """
    novel_templates = [
        _get_template_id(e)
        for e in new_entries
        if _get_template_id(e) not in known_ids
    ]
    return {"novel_templates": novel_templates}


def analyze_log_signals_windowed(
    window_1: list[LogEntry],
    window_2: list[LogEntry],
) -> LogBurstSignal:
    """Analisa surto de logs com janelas pré-divididas externamente.

    Útil quando o chamador já dividiu os logs por janela temporal
    (em vez de divisão por índice). Permite detectar aumento real
    de taxa de logs conforme o incidente evolui.

    Parâmetros
    ----------
    window_1 : list[LogEntry]
        Janela de baseline (período anterior).
    window_2 : list[LogEntry]
        Janela corrente (período mais recente).

    Retorna
    -------
    LogBurstSignal com burst_detected, rate_ratio, dominant_template e novel_templates.
    """
    known_ids: set[str] = {_get_template_id(e) for e in window_1}
    burst_result = compute_log_burst_rate(window_1, window_2)
    novel_result = detect_novel_templates(known_ids, window_2)

    return LogBurstSignal(
        burst_detected=burst_result["burst_detected"],
        rate_ratio=burst_result["rate_ratio"],
        dominant_template=burst_result["dominant_template"],
        novel_templates=novel_result["novel_templates"],
    )


def analyze_log_signals(log_entries: list[LogEntry]) -> LogBurstSignal:
    """Analisa o fluxo de logs e retorna um sinal combinado de surto e novidade.

    Divide as entradas em duas metades (W1=baseline, W2=corrente),
    computa a taxa de surto e detecta templates novos.

    Parâmetros
    ----------
    log_entries : list[LogEntry]
        Lista completa de entradas de log a analisar.

    Retorna
    -------
    LogBurstSignal com os resultados combinados.
    """
    mid = len(log_entries) // 2
    window_1 = log_entries[:mid]
    window_2 = log_entries[mid:]

    known_ids: set[str] = {_get_template_id(e) for e in window_1}

    burst_result = compute_log_burst_rate(window_1, window_2)
    novel_result = detect_novel_templates(known_ids, window_2)

    return LogBurstSignal(
        burst_detected=burst_result["burst_detected"],
        rate_ratio=burst_result["rate_ratio"],
        dominant_template=burst_result["dominant_template"],
        novel_templates=novel_result["novel_templates"],
    )
