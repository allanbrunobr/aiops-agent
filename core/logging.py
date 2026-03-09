"""Structured logging com structlog para o AIOps Agent.

Fornece get_logger(), configure_logging() e bind_context()
para logging estruturado em todo o pipeline.
"""
from __future__ import annotations

import logging
import sys


class StructuredLogger:
    """Logger estruturado simples que emula interface do structlog."""

    def __init__(self, name: str):
        self._logger = logging.getLogger(name)

    def info(self, event: str, **kwargs: object) -> None:
        self._logger.info(event, extra=kwargs)

    def warning(self, event: str, **kwargs: object) -> None:
        self._logger.warning(event, extra=kwargs)

    def error(self, event: str, **kwargs: object) -> None:
        self._logger.error(event, extra=kwargs)

    def debug(self, event: str, **kwargs: object) -> None:
        self._logger.debug(event, extra=kwargs)

    warn = warning


_loggers: dict[str, StructuredLogger] = {}


def get_logger(name: str) -> StructuredLogger:
    """Retorna logger estruturado para o modulo."""
    if name not in _loggers:
        _loggers[name] = StructuredLogger(name)
    return _loggers[name]


def configure_logging(level: str = "INFO") -> None:
    """Configura logging global."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )


def bind_context(**kwargs: object) -> None:
    """Vincula contexto ao logger atual (placeholder para structlog)."""
    pass
