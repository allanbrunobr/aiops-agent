"""Shim temporario — ``InMemoryStore`` migrou para ``adapters/persistence/``.

A implementacao concreta vive em
``adapters.persistence.in_memory_store`` desde a Fase 2d. Este modulo
permanece como ponte ate a Fase 2e remover os shims legados.
"""

from __future__ import annotations

from adapters.persistence.in_memory_store import InMemoryStore

__all__ = ["InMemoryStore"]
