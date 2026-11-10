"""Conversão única de confiança de diagnóstico para score numérico.

`confidence` chega como texto ("high" | "medium" | "low", do prompt e da
heurística) ou como número (0.0–1.0). Antes, a conversão estava espalhada
em quatro pontos com duas escalas diferentes — e um deles fazia
float("medium"), o que derrubava a persistência na memória institucional.

Puro: sem dependências fora da stdlib (ADR 0003).
"""
from __future__ import annotations

CONFIDENCE_SCORES: dict[str, float] = {"high": 0.85, "medium": 0.5, "low": 0.25}


def confidence_to_score(value: object, default: float = 0.0) -> float:
    """Converte a confiança de um diagnóstico em score no intervalo [0, 1].

    Aceita os rótulos de CONFIDENCE_SCORES (sem diferenciar maiúsculas),
    números e strings numéricas. Qualquer outra coisa vira `default`.
    """
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return min(1.0, max(0.0, float(value)))
    if isinstance(value, str):
        label = value.strip().lower()
        if label in CONFIDENCE_SCORES:
            return CONFIDENCE_SCORES[label]
        try:
            return min(1.0, max(0.0, float(label)))
        except ValueError:
            return default
    return default
