"""Authorization Server OAuth 2.1 com PKCE para o AIOps Agent.

Endpoints:
  POST /auth/token/register            — registra cliente e emite JWT
  POST /auth/token/introspect          — valida token ativo (RFC 7662)
  GET  /.well-known/oauth-authorization-server — discovery RFC 8414
  GET  /.well-known/jwks.json          — chaves públicas RSA (Phase 2, RFC 7517)

Baseado em: IETF draft-klrc-aiagent-auth-00 (março 2026).
Phase 2: suporte a RS256 assimétrico + JWKS endpoint para federação.
"""
from __future__ import annotations

import hashlib
import hmac
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from auth.jwt_validator import MIN_HMAC_KEY_BYTES, TokenClaims, issue_token, validate_token
from auth.pkce import verify_challenge
from auth.scopes import ALL_SCOPES
from auth.token_store import InMemoryTokenStore, TokenStore

import jwt as pyjwt

router = APIRouter(prefix="/auth", tags=["oauth"])

# Store compartilhado — substituível por RedisTokenStore em produção
_token_store: TokenStore = InMemoryTokenStore()

# Configuração injetável (preenchida por configure).
# Sem segredo de módulo: um literal default público assinaria tokens para qualquer um.
_jwt_secret: str = ""
_token_ttl: int = 3600
_allowed_clients: dict[str, list[str]] = {}  # client_id → scopes permitidos
# client_id → SHA-256 hex do segredo do cliente. Cliente fora deste mapa não
# recebe token: o client_id é público (config/defaults.yaml), não é credencial.
_client_secret_hashes: dict[str, str] = {}
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_base_url: str = "http://localhost:8000"
# aud/iss (SEC-11): vazios = tokens sem esses claims, como antes.
_issuer: str = ""
_audience: str = ""

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
    token_store: TokenStore | None = None,
    rsa_private_pem: str | None = None,
    rsa_public_pem: str | None = None,
    kid: str = "aiops-rs256-1",
    issuer: str = "",
    audience: str = "",
) -> None:
    """Configura o Authorization Server com os valores do OAuthConfig.

    Deve ser chamado durante o startup da aplicação antes de receber requests.

    Args:
        jwt_secret: Chave secreta para assinatura JWT HS256 (Phase 1).
        token_ttl: TTL dos tokens em segundos.
        allowed_clients: Lista de dicts com 'id' e 'scopes' dos clientes autorizados.
        base_url: URL base do servidor para o discovery endpoint.
        token_store: Implementação de TokenStore (padrão: InMemoryTokenStore).
        rsa_private_pem: Chave privada RSA PEM (Phase 2 — RS256). None = usar HS256.
        rsa_public_pem: Chave pública RSA PEM (Phase 2 — RS256). None = usar HS256.
        kid: Key ID para o JWK (Phase 2).
    """
    global _jwt_secret, _token_ttl, _allowed_clients, _base_url, _token_store
    global _rsa_private_pem, _rsa_public_pem, _kid, _jwk_set, _client_secret_hashes
    global _issuer, _audience
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
    _issuer = issuer
    _audience = audience
    _kid = kid
    if token_store is not None:
        _token_store = token_store
    # Empty or omitted allow-list means nobody is allowed. A previous
    # configure() must not keep clients when the new list is empty.
    _allowed_clients = {
        c["id"]: list(c.get("scopes", [])) for c in (allowed_clients or [])
    }
    # Só hashes bem formados entram; o resto equivale a "sem segredo".
    _client_secret_hashes = {
        c["id"]: c["secret_sha256"].lower()
        for c in (allowed_clients or [])
        if _SHA256_HEX.match(str(c.get("secret_sha256", "")).lower())
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

class RegisterRequest(BaseModel):
    """Body do endpoint POST /auth/token/register."""
    client_id: str
    client_secret: str = ""
    scopes: list[str]
    code_verifier: str = ""
    code_challenge: str
    code_challenge_method: str = "S256"


class RegisterResponse(BaseModel):
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

@router.post("/token/register", response_model=RegisterResponse)
async def register(body: RegisterRequest) -> RegisterResponse:
    """Registra um agente cliente e emite um access_token JWT.

    Valida:
    1. client_id está na allow-list (lista vazia não autoriza ninguém)
    1b. client_secret confere com o SHA-256 configurado para o cliente
        (client_secret_post). Cliente sem hash configurado não recebe token.
    2. Scopes solicitados são subconjunto dos scopes do cliente
    3. code_challenge_method é S256 (único suportado)
    4. code_verifier corresponde ao code_challenge (PKCE S256)

    O PKCE sozinho não autentica: verifier e challenge chegam no mesmo corpo,
    então qualquer requisitante gera o par. Quem autentica é o client_secret.

    O code_challenge só é armazenado depois que o verifier confere.
    """
    # issue_token assina só HS256. Com apenas o par RSA configurado, a emissão
    # falharia com 500; recusa explícita até existir emissão RS256.
    if len(_jwt_secret.encode("utf-8")) < MIN_HMAC_KEY_BYTES:
        raise HTTPException(
            status_code=503,
            detail="OAuth HS256 signing key is not configured (RS256 issuance is not implemented)",
        )

    # 1. Allow-list fechada. Ids publicados no YAML não bastam se não
    # foram carregados nesta instância, e lista vazia não é "qualquer um".
    if body.client_id not in _allowed_clients:
        raise HTTPException(
            status_code=401,
            detail=f"Unknown client_id: '{body.client_id}'",
        )

    # 1b. Autenticação do cliente. Comparação em tempo constante; a mesma
    # resposta para segredo ausente, errado ou não configurado.
    expected = _client_secret_hashes.get(body.client_id)
    provided = hashlib.sha256(body.client_secret.encode("utf-8")).hexdigest()
    if not expected or not body.client_secret or not hmac.compare_digest(provided, expected):
        raise HTTPException(
            status_code=401,
            detail="Invalid client credentials",
        )

    # 2. Valida scopes
    permitted = set(_allowed_clients.get(body.client_id, []))
    requested = set(body.scopes)
    disallowed = requested - permitted
    if disallowed:
        raise HTTPException(
            status_code=403,
            detail=f"Scopes not permitted for client: {sorted(disallowed)}",
        )

    # 3. Valida método PKCE
    if body.code_challenge_method != "S256":
        raise HTTPException(
            status_code=400,
            detail="Only code_challenge_method=S256 is supported",
        )

    # 4. O cliente precisa provar o verifier. Um challenge arbitrário
    # (deadbeef, texto que não é S256) não emite token.
    if not body.code_verifier or not verify_challenge(body.code_verifier, body.code_challenge):
        raise HTTPException(
            status_code=400,
            detail="PKCE verification failed",
        )

    _token_store.save_challenge(body.client_id, body.code_challenge)

    effective_scopes = body.scopes or _allowed_clients.get(body.client_id, [])
    token = issue_token(
        agent_id=body.client_id,
        scopes=effective_scopes,
        secret=_jwt_secret,
        ttl=_token_ttl,
        issuer=_issuer or None,
        audience=_audience or None,
    )

    return RegisterResponse(
        access_token=token,
        expires_in=_token_ttl,
        scope=" ".join(effective_scopes),
    )


@router.post("/token/introspect")
async def introspect(body: IntrospectRequest) -> dict:
    """Valida um token e retorna seus claims (RFC 7662).

    Usado pelo MCP Server para validação de tokens ativos.
    Retorna active=False (sem erro 401) para tokens inválidos/expirados.
    """
    try:
        claims: TokenClaims = validate_token(
            body.token, _jwt_secret, issuer=_issuer or None, audience=_audience or None,
        )
        return {
            "active": True,
            "sub": claims.sub,
            "scope": " ".join(claims.scopes),
            "exp": claims.exp,
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
        "issuer": _base_url,
        "token_endpoint": f"{_base_url}/auth/token/register",
        "introspection_endpoint": f"{_base_url}/auth/token/introspect",
        "scopes_supported": ALL_SCOPES,
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
        "grant_types_supported": ["client_credentials"],
    }
    # Phase 2: adiciona jwks_uri e algoritmos assimétricos se RS256 estiver configurado
    if _rsa_public_pem:
        meta["jwks_uri"] = f"{_base_url}/auth/.well-known/jwks.json"
        meta["id_token_signing_alg_values_supported"] = ["RS256"]
    return meta
