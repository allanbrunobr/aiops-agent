"""Shim temporario — Protocols redirecionados para suas novas localizacoes.

``IncidentStore`` migrou para ``application.ports.incident_store`` (Fase 2d).
``CredentialProvider`` permanece definido aqui ate uma fase futura decidir
seu novo lar.

Este shim sera removido na Fase 2e. Imports remanescentes em testes podem
continuar usando ``core.protocols`` enquanto durar a transicao.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from application.ports.incident_store import IncidentStore

__all__ = ["CredentialProvider", "IncidentStore"]


@runtime_checkable
class CredentialProvider(Protocol):
    """Protocolo para recuperacao de credenciais."""

    def get(self, key: str) -> str | None:
        """Recupera credencial por chave. Retorna None se nao encontrada."""
        ...
