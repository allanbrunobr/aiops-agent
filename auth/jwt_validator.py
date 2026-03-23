"""Issue e validação de JWT para o Authorization Server do AIOps Agent.

Usa PyJWT com algoritmo HS256 por padrão (adequado para servidor único).
Para produção multi-instância, migrar para RS256 + chaves assimétricas.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import jwt


@dataclass(frozen=True)
class TokenClaims:
    """Claims extraídas de um JWT validado."""

    sub: str                   # agent-id (ex: "assistant-agent")
    scopes: list[str] = field(default_factory=list)
    exp: int = 0


def issue_token(
    agent_id: str,
    scopes: list[str],
    secret: str,
    ttl: int = 3600,
    algorithm: str = "HS256",
) -> str:
    """Emite um JWT assinado com os claims do agente.

    Args:
        agent_id: Identificador único do agente (sub claim).
        scopes: Lista de scopes autorizados (ex: ["aiops:diagnose", "aiops:search"]).
        secret: Chave secreta para assinatura HS256.
        ttl: Tempo de vida em segundos (padrão 3600).
        algorithm: Algoritmo de assinatura (padrão HS256).

    Returns:
        JWT string compacto.
    """
    payload = {
        "sub": agent_id,
        "scope": " ".join(scopes),
        "exp": int(time.time()) + ttl,
        "iat": int(time.time()),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def validate_token(
    token: str,
    secret: str,
    algorithm: str = "HS256",
) -> TokenClaims:
    """Valida e decodifica um JWT, retornando os claims.

    Args:
        token: JWT string (sem prefixo "Bearer ").
        secret: Chave secreta usada na assinatura.
        algorithm: Algoritmo esperado (padrão HS256).

    Returns:
        TokenClaims com sub, scopes e exp.

    Raises:
        jwt.ExpiredSignatureError: Token expirado.
        jwt.InvalidTokenError: Token inválido (assinatura, formato, etc.).
    """
    payload = jwt.decode(token, secret, algorithms=[algorithm])
    raw_scope = payload.get("scope", "")
    scopes = raw_scope.split() if raw_scope else []
    return TokenClaims(
        sub=payload["sub"],
        scopes=scopes,
        exp=int(payload.get("exp", 0)),
    )
