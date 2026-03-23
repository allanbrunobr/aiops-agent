"""
Transporte SSE do MCP Server para o AIOps Agent.

Monta endpoints SSE do MCP em uma aplicação FastAPI existente.
Autenticação via Bearer JWT (OAuth 2.1) ou header X-Client-Id (legado).

auth_method="oauth21" — valida Bearer JWT, extrai sub + scopes do token.
auth_method="header"  — comportamento legado: X-Client-Id obrigatório.
auth_method="none"    — sem autenticação (confiança implícita, dev only).

História 5.1: Fundação do servidor MCP com transporte dual.
Referência: architecture.md — MCP Transport Decision, Lines 334-341.
"""

from __future__ import annotations

import jwt as pyjwt
from fastapi import FastAPI, Request, Response
from mcp.server.sse import SseServerTransport

from auth.jwt_validator import validate_token
from mcp_server.server import _current_client_id, _current_scopes, create_server

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
    jwt_secret = "dev-secret-change-in-production"
    if config is not None:
        auth_method = getattr(config, "auth_method", "header")
        oauth_cfg = getattr(config, "oauth", None)
        if oauth_cfg is not None:
            jwt_secret = getattr(oauth_cfg, "jwt_secret", jwt_secret)

    @app.get("/mcp/sse")
    async def sse_endpoint(request: Request) -> Response:
        """Endpoint SSE — cliente conecta aqui para stream de eventos MCP."""

        if auth_method == "oauth21":
            # OAuth 2.1: Bearer JWT obrigatório
            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                return Response(
                    status_code=401,
                    content='{"error":"unauthorized","detail":"Bearer token required"}',
                    media_type="application/json",
                )
            raw_token = auth_header[7:]
            try:
                claims = validate_token(raw_token, jwt_secret)
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
            effective_client_id = claims.sub
            granted_scopes = claims.scopes

        elif auth_method == "header":
            # Legado: X-Client-Id header
            client_id = request.headers.get("x-client-id")
            if not client_id:
                return Response(
                    status_code=401,
                    content="X-Client-Id header required",
                )
            effective_client_id = client_id
            granted_scopes = []  # sem scope enforcement no modo legado

        else:
            # auth_method="none": dev/test only
            effective_client_id = "anonymous"
            granted_scopes = []

        # Propaga client_id e scopes via ContextVar
        token_ctx = _current_client_id.set(effective_client_id)
        scopes_ctx = _current_scopes.set(granted_scopes)
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

    @app.post("/mcp/messages")
    async def message_endpoint(request: Request) -> Response:
        """Endpoint para mensagens MCP do cliente (POST)."""
        await _sse_transport.handle_post_message(  # type: ignore[union-attr]
            request.scope, request.receive, request._send
        )
