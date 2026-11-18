"""Authorization Server OAuth 2.1 com PKCE para o AIOps Agent.

Endpoints:
  POST /auth/authorize                 — recebe o code_challenge (S256) e emite code
  POST /auth/token                     — troca code + code_verifier por JWT (PKCE real)
  POST /auth/token/introspect          — valida token ativo (RFC 7662)
  GET  /.well-known/oauth-authorization-server — discovery RFC 8414
  GET  /.well-known/jwks.json          — chaves públicas RSA (Phase 2, RFC 7517)

Baseado em: IETF draft-klrc-aiagent-auth-00 (março 2026).
F4-1: authorize/token separados — a prova PKCE (verifier) acontece no
exchange, não na mesma request do challenge; code de uso único vinculado
a client+scopes+challenge.
Phase 2: suporte a RS256 assimétrico + JWKS endpoint para federação.
"""

from __future__ import annotations

import secrets

import jwt as pyjwt
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth.jwt_validator import (
    MIN_HMAC_KEY_BYTES,
    TokenClaims,
    issue_token,
    validate_token,
)
from auth.pkce import verify_challenge
from auth.scopes import ALL_SCOPES
from auth.token_store import InMemoryTokenStore, TokenStore

router = APIRouter(prefix="/auth", tags=["oauth"])

# Store compartilhado — substituível por RedisTokenStore em produção
_token_store: TokenStore = InMemoryTokenStore()

# Configuração injetável (preenchida por configure).
# Sem segredo de módulo: um literal default público assinaria tokens para qualquer um.
_jwt_secret: str = ""
_token_ttl: int = 3600
_allowed_clients: dict[str, list[str]] = {}  # client_id → scopes permitidos
_base_url: str = "http://localhost:8000"
_issuer: str = ""

# Phase 2 — RS256 assimétrico
_rsa_private_pem: str | None = None
_rsa_public_pem: str | None = None
_kid: str = "aiops-rs256-1"
_jwk_set: dict = {"keys": []}  # RFC 7517 JWKS


def configure(
    jwt_secret: str,
    token_ttl: int = 3600,
    allowed_clients: list[dict] | None = None,
    base_url: str = "http://localhost:8000",
    issuer: str = "",
    token_store: TokenStore | None = None,
    rsa_private_pem: str | None = None,
    rsa_public_pem: str | None = None,
    kid: str = "aiops-rs256-1",
) -> None:
    """Configura o Authorization Server com os valores do OAuthConfig.

    Deve ser chamado durante o startup da aplicação antes de receber requests.

    Args:
        jwt_secret: Chave secreta para assinatura JWT HS256 (Phase 1).
        token_ttl: TTL dos tokens em segundos.
        allowed_clients: Lista de dicts com 'id' e 'scopes' dos clientes autorizados.
        base_url: URL base do servidor para o discovery endpoint.
        issuer: Claim iss dos tokens e issuer anunciado no discovery (RFC 8414).
            Vazio (padrão) deriva de base_url (comportamento histórico); um valor
            explícito sobrescreve — fonte única compartilhada com os resource
            servers via OAuthConfig.issuer (pós-revisão #5).
        token_store: Implementação de TokenStore (padrão: InMemoryTokenStore).
        rsa_private_pem: Chave privada RSA PEM (Phase 2 — RS256). None = usar HS256.
        rsa_public_pem: Chave pública RSA PEM (Phase 2 — RS256). None = usar HS256.
        kid: Key ID para o JWK (Phase 2).
    """
    global _jwt_secret, _token_ttl, _allowed_clients, _base_url, _token_store
    global _rsa_private_pem, _rsa_public_pem, _kid, _jwk_set, _issuer
    # HS256 is the signer unless an RSA pair is supplied. A short or empty
    # HMAC key must not be stored: PyJWT warns and still signs keys under 32 bytes.
    if not (rsa_private_pem and rsa_public_pem):
        if len(jwt_secret.encode("utf-8")) < MIN_HMAC_KEY_BYTES:
            raise ValueError(
                f"jwt_secret must be at least {MIN_HMAC_KEY_BYTES} bytes for HS256"
            )
    _jwt_secret = jwt_secret
    _token_ttl = token_ttl
    _base_url = base_url
    _issuer = issuer or base_url or "aiops-agent"
    _kid = kid
    if token_store is not None:
        _token_store = token_store
    # Empty or omitted allow-list means nobody is allowed. A previous
    # configure() must not keep clients when the new list is empty.
    _allowed_clients = {
        c["id"]: list(c.get("scopes", [])) for c in (allowed_clients or [])
    }
    if rsa_private_pem and rsa_public_pem:
        from auth.keys import public_key_to_jwk

        _rsa_private_pem = rsa_private_pem
        _rsa_public_pem = rsa_public_pem
        _jwk_set = {"keys": [public_key_to_jwk(rsa_public_pem, kid=kid)]}
    else:
        # Limpa estado RSA se não fornecido (reset explícito)
        _rsa_private_pem = None
        _rsa_public_pem = None
        _jwk_set = {"keys": []}


# ---------------------------------------------------------------------------
# Schemas de request/response
# ---------------------------------------------------------------------------


class AuthorizeRequest(BaseModel):
    """Body do endpoint POST /auth/authorize — só o challenge, nunca o verifier."""

    client_id: str
    scopes: list[str]
    code_challenge: str
    code_challenge_method: str = "S256"
    audience: str = "mcp"  # "mcp" | "a2a" — audiência do token (F4-3)


class AuthorizeResponse(BaseModel):
    """Authorization code de uso único vinculado ao challenge."""

    code: str
    expires_in: int = 300


class TokenRequest(BaseModel):
    """Body do endpoint POST /auth/token — exchange com o verifier (prova PKCE)."""

    client_id: str
    code: str
    code_verifier: str


class TokenResponse(BaseModel):
    """Resposta com JWT emitido."""

    access_token: str
    token_type: str = "Bearer"
    expires_in: int
    scope: str


class IntrospectRequest(BaseModel):
    """Body do endpoint POST /auth/token/introspect."""

    token: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


def _signing_key_ready() -> bool:
    return len(_jwt_secret.encode("utf-8")) >= MIN_HMAC_KEY_BYTES or bool(
        _rsa_private_pem
    )


def _signing_algorithm() -> str:
    """RS256 de verdade quando o par RSA está configurado (F4-3).

    Antes, o metadata anunciava RS256 mas issue_token usava sempre HS256 —
    e RSA + secret curto estourava InvalidKeyError (500).
    """
    return "RS256" if _rsa_private_pem else "HS256"


def _signing_key() -> str:
    return _rsa_private_pem or _jwt_secret


def _verification_key() -> str:
    return _rsa_public_pem or _jwt_secret


@router.post("/authorize", response_model=AuthorizeResponse)
async def authorize(body: AuthorizeRequest) -> AuthorizeResponse:
    """Recebe o code_challenge (S256) e emite um authorization code de uso único.

    Valida allow-list, scopes e método; vincula code → client+scopes+challenge.
    O verifier NUNCA é aceito aqui — a prova PKCE acontece no /token (F4-1).
    """
    if not _signing_key_ready():
        raise HTTPException(
            status_code=503,
            detail="OAuth signing key is not configured",
        )

    # Allow-list fechada: lista vazia não autoriza ninguém
    if body.client_id not in _allowed_clients:
        raise HTTPException(
            status_code=401,
            detail=f"Unknown client_id: '{body.client_id}'",
        )

    permitted = set(_allowed_clients.get(body.client_id, []))
    disallowed = set(body.scopes) - permitted
    if disallowed:
        raise HTTPException(
            status_code=403,
            detail=f"Scopes not permitted for client: {sorted(disallowed)}",
        )

    if body.code_challenge_method != "S256":
        raise HTTPException(
            status_code=400,
            detail="Only code_challenge_method=S256 is supported",
        )
    if not body.code_challenge:
        raise HTTPException(status_code=400, detail="code_challenge is required")
    if body.audience not in ("mcp", "a2a"):
        raise HTTPException(status_code=400, detail="audience must be 'mcp' or 'a2a'")

    effective_scopes = body.scopes or _allowed_clients.get(body.client_id, [])
    code = secrets.token_urlsafe(32)
    _token_store.save_code(
        code,
        body.client_id,
        effective_scopes,
        body.code_challenge,
        audience=body.audience,
        ttl=300,
    )
    return AuthorizeResponse(code=code, expires_in=300)


@router.post("/token", response_model=TokenResponse)
async def token(body: TokenRequest) -> TokenResponse:
    """Troca authorization code + code_verifier por access_token JWT.

    O code é consumido (uso único); o verifier precisa corresponder ao
    challenge armazenado no /authorize; o client_id precisa ser o mesmo
    do vínculo. Scopes vêm do code, não da request (F4-1).
    """
    if not _signing_key_ready():
        raise HTTPException(
            status_code=503,
            detail="OAuth signing key is not configured",
        )

    entry = _token_store.pop_code(body.code)
    if entry is None:
        raise HTTPException(
            status_code=400, detail="Invalid or expired authorization code"
        )
    if entry["client_id"] != body.client_id:
        raise HTTPException(
            status_code=400, detail="Code was issued to a different client"
        )
    if not body.code_verifier or not verify_challenge(
        body.code_verifier, entry["challenge"]
    ):
        raise HTTPException(status_code=400, detail="PKCE verification failed")

    issued = issue_token(
        agent_id=body.client_id,
        scopes=entry["scopes"],
        secret=_signing_key(),
        ttl=_token_ttl,
        algorithm=_signing_algorithm(),
        issuer=_issuer,
        audience=entry.get("audience", "mcp"),
    )
    return TokenResponse(
        access_token=issued,
        expires_in=_token_ttl,
        scope=" ".join(entry["scopes"]),
    )


@router.post("/token/introspect")
async def introspect(body: IntrospectRequest) -> dict:
    """Valida um token e retorna seus claims (RFC 7662).

    Usado pelo MCP Server para validação de tokens ativos.
    Retorna active=False (sem erro 401) para tokens inválidos/expirados.
    """
    try:
        # Introspecção aceita ambas as audiências (mcp/a2a), mas exige iss
        claims: TokenClaims = validate_token(
            body.token,
            _verification_key(),
            algorithm=_signing_algorithm(),
            audience=["mcp", "a2a"],
            issuer=_issuer,
        )
        return {
            "active": True,
            "sub": claims.sub,
            "scope": " ".join(claims.scopes),
            "exp": claims.exp,
            "aud": claims.aud,
        }
    except pyjwt.ExpiredSignatureError:
        return {"active": False, "reason": "token_expired"}
    except pyjwt.InvalidTokenError:
        return {"active": False, "reason": "token_invalid"}


@router.get("/.well-known/jwks.json")
async def jwks() -> dict:
    """JWKS endpoint RFC 7517 — publica chaves públicas RSA para validação externa.

    Retorna o conjunto de chaves públicas que IdPs externos e agentes parceiros
    podem usar para verificar tokens emitidos por este servidor (Phase 2 — RS256).
    Retorna conjunto vazio se RS256 não estiver configurado.
    """
    return _jwk_set


@router.get("/.well-known/oauth-authorization-server")
async def discovery() -> dict:
    """Discovery endpoint RFC 8414 — permite que agentes descubram os endpoints."""
    meta: dict = {
        "issuer": _issuer,
        "authorization_endpoint": f"{_base_url}/auth/authorize",
        "token_endpoint": f"{_base_url}/auth/token",
        "introspection_endpoint": f"{_base_url}/auth/token/introspect",
        "scopes_supported": ALL_SCOPES,
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "grant_types_supported": ["authorization_code"],
    }
    # Phase 2: adiciona jwks_uri e algoritmos assimétricos se RS256 estiver configurado
    if _rsa_public_pem:
        meta["jwks_uri"] = f"{_base_url}/auth/.well-known/jwks.json"
        meta["id_token_signing_alg_values_supported"] = ["RS256"]
    return meta
