"""Confiança do diagnóstico: rótulo textual ou número → score numérico [0, 1].

Fonte única para o mapa que existia copiado em vários lugares (CLI V0 ×2,
MCP tools, persistência na memória institucional) — F5-2 / F07. Antes um
deles fazia ``float("medium")``, derrubando a persistência. Puro stdlib
(ADR 0003), como todo ``domain/``.
"""

from __future__ import annotations

import math
from typing import Any

CONFIDENCE_SCORES: dict[str, float] = {"high": 0.85, "medium": 0.5, "low": 0.25}


def confidence_to_score(value: Any, default: float = 0.0) -> float:
    """Converte a confiança de um diagnóstico em float no intervalo [0, 1].

    - Numérico (int/float) é clampado a [0, 1]; NaN devolve ``default``
      (inf/-inf clampam para 1.0/0.0).
    - Texto é mapeado por :data:`CONFIDENCE_SCORES` (case-insensitive) ou,
      se for string numérica ("0.7"), parseada e clampada.
    - Qualquer outra coisa (None, rótulo desconhecido) devolve ``default``.
    """
    if isinstance(value, bool):  # bool é int: não tratar True como 1.0
        return default
    if isinstance(value, (int, float)):
        num = float(value)
        if math.isnan(num):
            return default
        return max(0.0, min(1.0, num))
    if isinstance(value, str):
        label = value.strip().lower()
        if label in CONFIDENCE_SCORES:
            return CONFIDENCE_SCORES[label]
        try:
            num = float(label)
        except ValueError:
            return default
        if math.isnan(num):
            return default
        return max(0.0, min(1.0, num))
    return default


__all__ = ["CONFIDENCE_SCORES", "confidence_to_score"]
