"""Protocol e implementações de TokenStore para o Authorization Server.

O Protocol permite substituir InMemoryTokenStore por RedisTokenStore em
produção sem alterar o código do servidor.
"""

from __future__ import annotations

import time
from typing import Protocol, runtime_checkable


@runtime_checkable
class TokenStore(Protocol):
    """Interface para armazenamento de code_challenges e authorization codes."""

    def save_challenge(self, client_id: str, challenge: str, ttl: int = 300) -> None:
        """Persiste o code_challenge com TTL (padrão 5 minutos)."""
        ...

    def pop_challenge(self, client_id: str) -> str | None:
        """Remove e retorna o challenge do client_id, ou None se expirado/ausente."""
        ...

    def save_code(
        self,
        code: str,
        client_id: str,
        scopes: list[str],
        challenge: str,
        audience: str = "mcp",
        ttl: int = 300,
    ) -> None:
        """Persiste authorization code vinculado a client+scopes+challenge+aud."""
        ...

    def pop_code(self, code: str) -> dict | None:
        """Remove e retorna os dados do code (uso único), ou None se expirado/ausente."""
        ...


class InMemoryTokenStore:
    """Implementação em memória — adequada para servidor único.

    Para produção com múltiplas instâncias, substituir por RedisTokenStore
    implementando o mesmo Protocol.
    """

    def __init__(self) -> None:
        # {client_id: (challenge, expires_at)}
        self._store: dict[str, tuple[str, float]] = {}
        # {code: (client_id, scopes, challenge, audience, expires_at)} — F4-1/F4-3
        self._codes: dict[str, tuple[str, list[str], str, str, float]] = {}

    def save_challenge(self, client_id: str, challenge: str, ttl: int = 300) -> None:
        """Armazena challenge com expiração absoluta."""
        self._store[client_id] = (challenge, time.time() + ttl)

    def pop_challenge(self, client_id: str) -> str | None:
        """Remove e retorna challenge se ainda válido, None caso contrário."""
        entry = self._store.pop(client_id, None)
        if entry is None:
            return None
        challenge, expires_at = entry
        if time.time() > expires_at:
            return None
        return challenge

    def save_code(
        self,
        code: str,
        client_id: str,
        scopes: list[str],
        challenge: str,
        audience: str = "mcp",
        ttl: int = 300,
    ) -> None:
        """Armazena authorization code com vínculo e expiração absoluta."""
        self._codes[code] = (
            client_id,
            list(scopes),
            challenge,
            audience,
            time.time() + ttl,
        )

    def pop_code(self, code: str) -> dict | None:
        """Remove e retorna os dados do code (uso único), None se expirado/ausente."""
        entry = self._codes.pop(code, None)
        if entry is None:
            return None
        client_id, scopes, challenge, audience, expires_at = entry
        if time.time() > expires_at:
            return None
        return {
            "client_id": client_id,
            "scopes": scopes,
            "challenge": challenge,
            "audience": audience,
        }
