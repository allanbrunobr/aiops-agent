"""Porta ``CredentialProvider`` — contrato de recuperacao de credenciais.

Define o Protocol consumido pela camada de aplicacao para obter segredos
(tokens, API keys) sem acoplamento a uma fonte concreta. Implementacoes
vivem em ``adapters/`` (env vars, secret managers, vaults, etc.).

Decorado com ``@runtime_checkable`` para permitir validacao no
composition root via ``isinstance()``.
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
