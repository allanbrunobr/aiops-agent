"""
Transporte SSE do MCP Server para o AIOps Agent.

Monta endpoints SSE do MCP em uma aplicação FastAPI existente.
Autenticação via Bearer JWT (OAuth 2.1) ou header X-Client-Id (legado).

auth_method="oauth21"   — valida Bearer JWT HS256 local, extrai sub + scopes.
auth_method="federated" — valida Bearer JWT RS256 de IdPs externos via OIDC (Phase 2).
auth_method="header"    — comportamento legado: X-Client-Id obrigatório.
auth_method="none"      — sem autenticação (confiança implícita, dev only).

História 5.1: Fundação do servidor MCP com transporte dual.
Phase 2: auth_method="federated" com OIDCValidator e trusted_issuers.
Referência: architecture.md — MCP Transport Decision, Lines 334-341.
"""

from __future__ import annotations

import jwt as pyjwt
from fastapi import FastAPI, Request, Response
from mcp.server.sse import SseServerTransport

from auth.jwt_validator import MIN_HMAC_KEY_BYTES, validate_token
from interfaces.mcp.server import (
    _current_client_id,
    _current_scopes,
    _enforce_scopes,
    create_server,
)

# Estado do módulo — transporte e servidor MCP compartilhados
_sse_transport: SseServerTransport | None = None
_mcp_server = None


def mount_sse(
    app: FastAPI,
    config: object | None = None,
    **server_deps: object,
) -> None:
    """Monta endpoints SSE do MCP no app FastAPI existente.

    Adiciona rotas:
      GET  /mcp/sse      — stream SSE para cliente
      POST /mcp/messages — mensagens MCP do cliente

    Autenticação configurável via config.auth_method:
      "oauth21" — Bearer JWT obrigatório (padrão recomendado)
      "header"  — X-Client-Id legado
      "none"    — sem autenticação (dev/test only)

    Args:
        app: Aplicação FastAPI onde os endpoints serão montados.
        config: McpConfig com configurações (porta, auth_method, oauth, etc.).
        **server_deps: Dependências para create_server() (retriever, etc.).
    """
    global _sse_transport, _mcp_server

    _sse_transport = SseServerTransport("/mcp/messages")
    _mcp_server = create_server(config=config, **server_deps)

    auth_method = "header"
    jwt_secret = ""
    jwt_issuer = ""
    jwt_audience = ""
    allowed_header_clients: dict[str, list[str]] = {}
    _oidc_validator = None
    if config is not None:
        auth_method = getattr(config, "auth_method", "header")
        oauth_cfg = getattr(config, "oauth", None)
        if oauth_cfg is not None:
            jwt_secret = getattr(oauth_cfg, "jwt_secret", "") or ""
            jwt_issuer = getattr(oauth_cfg, "issuer", "") or ""
            jwt_audience = getattr(oauth_cfg, "audience", "") or ""
            for client in getattr(oauth_cfg, "allowed_clients", []) or []:
                if isinstance(client, dict):
                    client_id = client.get("id")
                    client_scopes = list(client.get("scopes") or [])
                else:
                    client_id = getattr(client, "id", None)
                    client_scopes = list(getattr(client, "scopes", []) or [])
                if client_id:
                    allowed_header_clients[str(client_id)] = client_scopes

        # Phase 2 — inicializa OIDCValidator se auth_method="federated"
        if auth_method == "federated":
            from auth.oidc_validator import OIDCValidator
            from auth.oidc_validator import TrustedIssuerConfig as _OIDCTrustedIssuerCfg
            import structlog as _structlog
            _log = _structlog.get_logger()

            trusted_raw = getattr(oauth_cfg, "trusted_issuers", []) if oauth_cfg else []
            if not trusted_raw:
                _log.warning(
                    "oidc.no_trusted_issuers",
                    detail="auth_method=federated mas trusted_issuers está vazio — nenhum token externo será aceito",
                )
            _oidc_issuers = [
                _OIDCTrustedIssuerCfg(
                    issuer=getattr(ti, "issuer", ""),
                    audience=getattr(ti, "audience", ""),
                    scope_claim=getattr(ti, "scope_claim", "scope"),
                )
                for ti in trusted_raw
            ]
            _oidc_validator = OIDCValidator(trusted_issuers=_oidc_issuers)

    def _authenticate(request: Request) -> tuple[str, list[str], bool] | Response:
        """Autentica GET /mcp/sse e POST /mcp/messages com a mesma regra.

        Retorna (client_id, scopes, enforce_scopes) ou uma Response 401.
        Lista vazia de scopes com enforce=True não libera ferramentas.
        """
        if auth_method == "oauth21":
            if len(jwt_secret.encode("utf-8")) < MIN_HMAC_KEY_BYTES:
                return Response(
                    status_code=401,
                    content='{"error":"unauthorized","detail":"OAuth signing key is not configured"}',
                    media_type="application/json",
                )
            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                return Response(
                    status_code=401,
                    content='{"error":"unauthorized","detail":"Bearer token required"}',
                    media_type="application/json",
                )
            raw_token = auth_header[7:]
            try:
                claims = validate_token(
                    raw_token, jwt_secret,
                    issuer=jwt_issuer or None, audience=jwt_audience or None,
                )
            except pyjwt.ExpiredSignatureError:
                return Response(
                    status_code=401,
                    content='{"error":"token_expired","detail":"JWT has expired"}',
                    media_type="application/json",
                )
            except pyjwt.InvalidTokenError:
                return Response(
                    status_code=401,
                    content='{"error":"invalid_token","detail":"JWT is invalid"}',
                    media_type="application/json",
                )
            return claims.sub, list(claims.scopes), True

        if auth_method == "header":
            client_id = request.headers.get("x-client-id")
            if not client_id or client_id not in allowed_header_clients:
                return Response(
                    status_code=401,
                    content="X-Client-Id header required",
                    media_type="text/plain",
                )
            return client_id, list(allowed_header_clients[client_id]), True

        return "anonymous", [], False

    @app.get("/mcp/sse")
    async def sse_endpoint(request: Request) -> Response:
        """Endpoint SSE — cliente conecta aqui para stream de eventos MCP."""

        enforce = True
        if auth_method == "federated":
            # Phase 2 — OIDC Federation: Bearer JWT RS256 de IdP externo
            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                return Response(
                    status_code=401,
                    content='{"error":"unauthorized","detail":"Bearer token required"}',
                    media_type="application/json",
                )
            raw_token = auth_header[7:]
            try:
                import asyncio
                claims = await _oidc_validator.validate(raw_token)  # type: ignore[union-attr]
            except pyjwt.ExpiredSignatureError:
                return Response(
                    status_code=401,
                    content='{"error":"token_expired","detail":"JWT has expired"}',
                    media_type="application/json",
                )
            except pyjwt.InvalidTokenError as exc:
                return Response(
                    status_code=401,
                    content=f'{{"error":"invalid_token","detail":"{exc}"}}',
                    media_type="application/json",
                )
            except Exception:
                return Response(
                    status_code=503,
                    content='{"error":"idp_unavailable","detail":"Falha ao contactar IdP externo"}',
                    media_type="application/json",
                )
            effective_client_id = claims.sub
            granted_scopes = list(claims.scopes)
            enforce = True

        else:
            authenticated = _authenticate(request)
            if isinstance(authenticated, Response):
                return authenticated
            effective_client_id, granted_scopes, enforce = authenticated

        # Propaga client_id e scopes via ContextVar
        token_ctx = _current_client_id.set(effective_client_id)
        scopes_ctx = _current_scopes.set(granted_scopes)
        enforce_ctx = _enforce_scopes.set(enforce)
        try:
            async with _sse_transport.connect_sse(  # type: ignore[union-attr]
                request.scope, request.receive, request._send
            ) as streams:
                await _mcp_server.run(
                    streams[0],
                    streams[1],
                    _mcp_server.create_initialization_options(),
                )
        finally:
            _current_client_id.reset(token_ctx)
            _current_scopes.reset(scopes_ctx)
            _enforce_scopes.reset(enforce_ctx)

    @app.post("/mcp/messages")
    async def message_endpoint(request: Request) -> Response:
        """Endpoint para mensagens MCP do cliente (POST).

        Repete a autenticação do GET. Sem isso o POST aceitava qualquer corpo.
        """
        if auth_method == "federated":
            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                return Response(
                    status_code=401,
                    content='{"error":"unauthorized","detail":"Bearer token required"}',
                    media_type="application/json",
                )
            try:
                await _oidc_validator.validate(auth_header[7:])  # type: ignore[union-attr]
            except pyjwt.ExpiredSignatureError:
                return Response(status_code=401, content='{"error":"token_expired"}', media_type="application/json")
            except pyjwt.InvalidTokenError:
                return Response(status_code=401, content='{"error":"invalid_token"}', media_type="application/json")
            except Exception:
                return Response(status_code=503, content='{"error":"idp_unavailable"}', media_type="application/json")
        else:
            authenticated = _authenticate(request)
            if isinstance(authenticated, Response):
                return authenticated
        await _sse_transport.handle_post_message(  # type: ignore[union-attr]
            request.scope, request.receive, request._send
        )
