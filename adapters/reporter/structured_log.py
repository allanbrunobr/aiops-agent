"""StructuredLogReporter — emite eventos como JSON via ``logging``.

Adapter de producao: cada evento vira um log entry com nome ``aiops.events``
e ``extra={"event": ..., "payload": {...}}``, consumivel por agregadores.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("aiops.events")


class StructuredLogReporter:
    """Emite eventos via ``logging.info`` com payload estruturado.

    Implementa o port ``Reporter`` via tipagem estrutural — sem herdar do
    Protocol, mantendo a mesma convencao de ``AnthropicLLMClient``.
    """

    def emit(self, event: str, **payload: Any) -> None:
        logger.info("event", extra={"event": event, "payload": payload})
