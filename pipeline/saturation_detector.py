from __future__ import annotations

"""
Detector de saturação (D4): platô no teto de uma métrica.

Precursor clássico de incidente que o EWMA-slope perde: réplicas do HPA no
``maxReplicas``, CPU pinned etc. Ali o slope ≈ 0 — a série parou de subir
justamente porque bateu no teto. O detector é determinístico, stdlib-only e
COMPLEMENTA (não substitui) os demais sinais preditivos.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

# Fração do teto a partir da qual a série é considerada "no topo".
DEFAULT_CEILING_FRACTION = 0.95
# K: nº mínimo de amostras CONSECUTIVAS (no fim da série) no platô.
DEFAULT_MIN_SUSTAINED = 5
# Variância baixa: (max - min) do platô, relativo ao teto, no máximo isto.
DEFAULT_MAX_SPREAD = 0.03
# Sem teto explícito (usa o máximo da janela) exige-se uma SUBIDA prévia:
# o mínimo da série precisa estar abaixo de ``fração * _RISE_FACTOR`` do teto.
# Sem isso, qualquer série flat seria "saturada" no próprio máximo.
_RISE_FACTOR = 0.9


@dataclass
class SaturationSignal:
    """Platô sustentado no topo da série."""

    ceiling: float
    level: float  # média do platô
    utilization: float  # level / ceiling
    sustained_samples: int
    ceiling_explicit: bool  # False = teto inferido do máximo da janela


def _values(points: Sequence[object]) -> list[float]:
    out: list[float] = []
    for p in points:
        v = p if isinstance(p, int | float) else getattr(p, "value", None)
        if isinstance(v, int | float) and math.isfinite(v):
            out.append(float(v))
    return out


def detect_saturation(
    points: Sequence[object],
    ceiling: float | None,
    *,
    min_sustained: int = DEFAULT_MIN_SUSTAINED,
    ceiling_fraction: float = DEFAULT_CEILING_FRACTION,
    max_spread: float = DEFAULT_MAX_SPREAD,
) -> SaturationSignal | None:
    """Detecta platô no teto sustentado por ≥ ``min_sustained`` amostras finais.

    ``points``: floats ou objetos com ``.value`` (ex.: MetricPoint), em ordem
    temporal. ``ceiling``: teto explícito (> 0); ``None`` → máximo da janela,
    caso em que também se exige subida prévia (ver ``_RISE_FACTOR``).
    Retorna None para série curta, platô abaixo do teto, variância alta ou
    platô que não está no fim da série (já saiu da saturação).
    """
    values = _values(points)
    if len(values) < min_sustained or min_sustained < 1:
        return None

    explicit = ceiling is not None and math.isfinite(ceiling) and ceiling > 0
    top = float(ceiling) if explicit and ceiling is not None else max(values)
    if top <= 0:
        return None
    if not explicit and min(values) > ceiling_fraction * _RISE_FACTOR * top:
        return None  # sem subida prévia: flat não é saturação

    floor = ceiling_fraction * top
    window: list[float] = []
    lo = hi = 0.0
    for v in reversed(values):
        if v < floor:
            break
        new_lo, new_hi = (v, v) if not window else (min(lo, v), max(hi, v))
        if (new_hi - new_lo) / top > max_spread:
            break
        lo, hi = new_lo, new_hi
        window.append(v)
    if len(window) < min_sustained:
        return None

    level = sum(window) / len(window)
    return SaturationSignal(
        ceiling=top,
        level=level,
        utilization=level / top,
        sustained_samples=len(window),
        ceiling_explicit=explicit,
    )


# Nível crônico acima do alvo (ex.: utilização > 80% do request): K amostras.
DEFAULT_SUSTAINED_LEVEL_SAMPLES = 15


def detect_sustained_level(
    points: Sequence[object],
    level: float,
    ceiling: float,
    *,
    min_sustained: int = DEFAULT_SUSTAINED_LEVEL_SAMPLES,
) -> SaturationSignal | None:
    """Nível sustentado ACIMA de ``level`` (ex.: alvo 80% do HPA), sem exigir platô.

    Cobre o creep lento: o slope é pequeno e o ETA fica longe, mas a série já
    vive acima do alvo. Exige que as ``min_sustained`` últimas amostras tenham
    MÉDIA ≥ ``level`` e MÍNIMO ≥ ``level`` menos 1% do ``ceiling`` (tolera
    ruído de uma amostra, não uma série que oscila abaixo do alvo). Retorna
    um ``SaturationSignal`` (``ceiling_explicit=True``) ou None.
    """
    values = _values(points)
    if min_sustained < 1 or len(values) < min_sustained or ceiling <= 0:
        return None
    tail = values[-min_sustained:]
    mean = sum(tail) / len(tail)
    if mean < level or min(tail) < level - 0.01 * ceiling:
        return None
    return SaturationSignal(
        ceiling=float(ceiling),
        level=mean,
        utilization=mean / ceiling,
        sustained_samples=len(tail),
        ceiling_explicit=True,
    )
