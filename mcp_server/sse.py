"""
Transporte SSE do MCP Server para o AIOps Agent.

Monta endpoints SSE do MCP em uma aplicação FastAPI existente.
Autenticação via header X-Client-Id (obrigatório para SSE).

História 5.1: Fundação do servidor MCP com transporte dual.
Referência: architecture.md — MCP Transport Decision, Lines 334-341.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, Response
from mcp.server.sse import SseServerTransport

from mcp_server.server import _current_client_id, create_server

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

    Autenticação: header X-Client-Id obrigatório.
    Client ID armazenado em ContextVar para rate limiting downstream.

    Args:
        app: Aplicação FastAPI onde os endpoints serão montados.
        config: McpConfig com configurações (porta, auth_method, etc.).
        **server_deps: Dependências para create_server() (retriever, etc.).
    """
    global _sse_transport, _mcp_server

    _sse_transport = SseServerTransport("/mcp/messages")
    _mcp_server = create_server(config=config, **server_deps)

    auth_method = "header"
    if config is not None:
        auth_method = getattr(config, "auth_method", "header")

    @app.get("/mcp/sse")
    async def sse_endpoint(request: Request) -> Response:
        """Endpoint SSE — cliente conecta aqui para stream de eventos MCP."""
        client_id = request.headers.get("x-client-id")

        # Autenticação: header X-Client-Id obrigatório quando auth_method="header"
        if auth_method == "header" and not client_id:
            return Response(
                status_code=401,
                content="X-Client-Id header required",
            )

        # Usa "anonymous" se auth_method="none"
        effective_client_id = client_id or "anonymous"

        # Armazena client_id no ContextVar para propagação
        token = _current_client_id.set(effective_client_id)
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
            _current_client_id.reset(token)

    @app.post("/mcp/messages")
    async def message_endpoint(request: Request) -> Response:
        """Endpoint para mensagens MCP do cliente (POST)."""
        await _sse_transport.handle_post_message(  # type: ignore[union-attr]
            request.scope, request.receive, request._send
        )
