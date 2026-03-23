"""Authorization Server OAuth 2.1 com PKCE para o AIOps Agent.

Endpoints:
  POST /auth/token/register       — registra cliente e emite JWT
  POST /auth/token/introspect     — valida token ativo (RFC 7662)
  GET  /.well-known/oauth-authorization-server — discovery RFC 8414

Baseado em: IETF draft-klrc-aiagent-auth-00 (março 2026).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from auth.jwt_validator import TokenClaims, issue_token, validate_token
from auth.pkce import verify_challenge
from auth.scopes import ALL_SCOPES
from auth.token_store import InMemoryTokenStore, TokenStore

import jwt as pyjwt

router = APIRouter(prefix="/auth", tags=["oauth"])

# Store compartilhado — substituível por RedisTokenStore em produção
_token_store: TokenStore = InMemoryTokenStore()

# Configuração injetável (preenchida por _configure)
_jwt_secret: str = "dev-secret-change-in-production"
_token_ttl: int = 3600
_allowed_clients: dict[str, list[str]] = {}  # client_id → scopes permitidos
_base_url: str = "http://localhost:8000"


def configure(
    jwt_secret: str,
    token_ttl: int = 3600,
    allowed_clients: list[dict] | None = None,
    base_url: str = "http://localhost:8000",
    token_store: TokenStore | None = None,
) -> None:
    """Configura o Authorization Server com os valores do OAuthConfig.

    Deve ser chamado durante o startup da aplicação antes de receber requests.

    Args:
        jwt_secret: Chave secreta para assinatura JWT (via env AIOPS__MCP__OAUTH__JWT_SECRET).
        token_ttl: TTL dos tokens em segundos.
        allowed_clients: Lista de dicts com 'id' e 'scopes' dos clientes autorizados.
        base_url: URL base do servidor para o discovery endpoint.
        token_store: Implementação de TokenStore (padrão: InMemoryTokenStore).
    """
    global _jwt_secret, _token_ttl, _allowed_clients, _base_url, _token_store
    _jwt_secret = jwt_secret
    _token_ttl = token_ttl
    _base_url = base_url
    if token_store is not None:
        _token_store = token_store
    if allowed_clients:
        _allowed_clients = {c["id"]: c.get("scopes", []) for c in allowed_clients}


# ---------------------------------------------------------------------------
# Schemas de request/response
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    """Body do endpoint POST /auth/token/register."""
    client_id: str
    scopes: list[str]
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
    1. client_id contra lista de clientes permitidos
    2. Scopes solicitados são subconjunto dos scopes do cliente
    3. code_challenge_method é S256 (único suportado)

    O code_challenge é armazenado no TokenStore com TTL=5min para
    uso futuro em flows que precisem de verificação do verifier.
    """
    # 1. Valida client_id
    if _allowed_clients and body.client_id not in _allowed_clients:
        raise HTTPException(
            status_code=401,
            detail=f"Unknown client_id: '{body.client_id}'",
        )

    # 2. Valida scopes
    if _allowed_clients:
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

    # Armazena challenge para uso futuro
    _token_store.save_challenge(body.client_id, body.code_challenge)

    # Emite JWT
    effective_scopes = body.scopes or (
        _allowed_clients.get(body.client_id, []) if _allowed_clients else []
    )
    token = issue_token(
        agent_id=body.client_id,
        scopes=effective_scopes,
        secret=_jwt_secret,
        ttl=_token_ttl,
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
        claims: TokenClaims = validate_token(body.token, _jwt_secret)
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


@router.get("/.well-known/oauth-authorization-server")
async def discovery() -> dict:
    """Discovery endpoint RFC 8414 — permite que agentes descubram os endpoints."""
    return {
        "issuer": _base_url,
        "token_endpoint": f"{_base_url}/auth/token/register",
        "introspection_endpoint": f"{_base_url}/auth/token/introspect",
        "scopes_supported": ALL_SCOPES,
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "grant_types_supported": ["client_credentials"],
    }
