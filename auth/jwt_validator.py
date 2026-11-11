"""Issue e validação de JWT para o Authorization Server do AIOps Agent.

Usa PyJWT com algoritmo HS256 por padrão (adequado para servidor único).
Para produção multi-instância, migrar para RS256 + chaves assimétricas.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import jwt

# RFC 7518 section 3.2: HMAC-SHA-256 key must be at least 256 bits.
MIN_HMAC_KEY_BYTES = 32


def _reject_short_hmac_key(secret: str, algorithm: str) -> None:
    """Refuse to sign or check an HMAC JWT with a key shorter than 32 bytes."""
    if not algorithm.upper().startswith("HS"):
        return
    key = secret.encode("utf-8")
    if len(key) < MIN_HMAC_KEY_BYTES:
        raise jwt.InvalidKeyError(
            "HMAC key must be at least 32 bytes (RFC 7518). "
            "An empty key is rejected by PyJWT and is not treated as accept-all."
        )


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
    *,
    issuer: str | None = None,
    audience: str | None = None,
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

    Raises:
        jwt.InvalidKeyError: HMAC key missing or shorter than 32 bytes.
    """
    _reject_short_hmac_key(secret, algorithm)
    payload = {
        "sub": agent_id,
        "scope": " ".join(scopes),
        "exp": int(time.time()) + ttl,
        "iat": int(time.time()),
    }
    # aud/iss só quando configurados (SEC-11); vazio mantém o formato anterior.
    if issuer:
        payload["iss"] = issuer
    if audience:
        payload["aud"] = audience
    return jwt.encode(payload, secret, algorithm=algorithm)


def validate_token(
    token: str,
    secret: str,
    algorithm: str = "HS256",
    *,
    issuer: str | None = None,
    audience: str | None = None,
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
        jwt.InvalidKeyError: HMAC key missing or shorter than 32 bytes.
    """
    _reject_short_hmac_key(secret, algorithm)
    # Com issuer/audience configurados, os claims são obrigatórios e conferidos.
    required = (["iss"] if issuer else []) + (["aud"] if audience else [])
    payload = jwt.decode(
        token, secret, algorithms=[algorithm],
        issuer=issuer or None, audience=audience or None,
        options={"require": required},
    )
    raw_scope = payload.get("scope", "")
    scopes = raw_scope.split() if raw_scope else []
    return TokenClaims(
        sub=payload["sub"],
        scopes=scopes,
        exp=int(payload.get("exp", 0)),
    )
