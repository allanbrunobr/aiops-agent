"""Protocols residuais em ``core/`` apos a Fase 2e.

``IncidentStore`` migrou para ``application.ports.incident_store`` e ``InMemoryStore``
para ``adapters.persistence.in_memory_store``. ``CredentialProvider`` permanece aqui
ate uma fase futura definir um lar dedicado para portas de infraestrutura.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["CredentialProvider"]


@runtime_checkable
class CredentialProvider(Protocol):
    """Protocolo para recuperacao de credenciais."""

    def get(self, key: str) -> str | None:
        """Recupera credencial por chave. Retorna None se nao encontrada."""
        ...
