"""Compatibilidade com a extensão `_aiops_rust` já instalada.

A P3-4 mudou a assinatura de `filter_metrics`/`filter_traces` (os limiares
passaram a ser argumentos). Um venv com a wheel antiga importava a extensão e
quebrava com TypeError no caminho padrão da CLI. Aqui, uma função que não
expõe os parâmetros esperados conta como indisponível: o pipeline usa o
fallback Python e avisa para recompilar (scripts/build_rust.sh).
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def has_params(module: object, name: str, *params: str) -> bool:
    """True se `module.name` existe e sua assinatura PyO3 tem todos os `params`."""
    fn = getattr(module, name, None)
    if fn is None:
        return False
    signature = getattr(fn, "__text_signature__", None) or ""
    missing = [p for p in params if p not in signature]
    if missing:
        _log.warning(
            "_aiops_rust.%s desatualizada (sem %s): usando o fallback Python. "
            "Recompile com scripts/build_rust.sh.", name, ", ".join(missing))
        return False
    return True
