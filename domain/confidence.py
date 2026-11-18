"""Confiança do diagnóstico: rótulo textual → score numérico.

Fonte única para o mapa que existia copiado em três lugares (CLI V0 ×2 e
MCP tools) — F5-2. Puro stdlib, como todo ``domain/``.
"""

from __future__ import annotations

import math
from typing import Any

CONFIDENCE_SCORES: dict[str, float] = {"high": 0.85, "medium": 0.5, "low": 0.25}


def confidence_to_score(value: Any, default: float = 0.0) -> float:
    """Converte a confiança de um diagnóstico em float no intervalo [0, 1].

    - Numérico (int/float) é clampado ao intervalo [0, 1]; não-finito NaN
      devolve ``default`` (inf/-inf clampam para 1.0/0.0).
    - Texto é mapeado por :data:`CONFIDENCE_SCORES` (case-insensitive).
    - Qualquer outra coisa (None, rótulo desconhecido) devolve ``default``.
    """
    if isinstance(value, bool):  # bool é int: não tratar True como 1.0
        return default
    if isinstance(value, (int, float)):
        num = float(value)
        if math.isnan(num):
            return default
        return max(0.0, min(1.0, num))
    if value is None:
        return default
    return CONFIDENCE_SCORES.get(str(value).strip().lower(), default)


__all__ = ["CONFIDENCE_SCORES", "confidence_to_score"]
