"""Adapters do Reporter port (Fase 2c).

- ``RichConsoleReporter``  — preserva o output visual da CLI.
- ``StructuredLogReporter`` — emite JSON via ``logging`` para producao.

``NullReporter`` vive em ``application/ports/reporter`` por ser o default
no-op que agentes usam quando nenhum reporter e injetado.
"""

from __future__ import annotations

from adapters.reporter.rich_console import RichConsoleReporter
from adapters.reporter.structured_log import StructuredLogReporter

__all__ = ["RichConsoleReporter", "StructuredLogReporter"]
