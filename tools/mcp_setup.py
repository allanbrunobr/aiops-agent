"""Bootstrap do servidor MCP — extraído de main.py (Story 4.1).

Encapsula inicialização do MCP Server com injeção de dependências,
permitindo que main.py delegue sem setup inline.
"""

from __future__ import annotations

from typing import Any


def setup_mcp_server(config: object = None) -> Any | None:
    """Cria e retorna um servidor MCP configurado.

    Lazy-imports dependências do MCP para não aumentar custo de startup.
    Retorna None se MCP estiver desabilitado na configuração.

    Args:
        config: Configuração da aplicação (McpConfig com enabled flag).

    Returns:
        Server MCP configurado, ou None se desabilitado/indisponível.
    """
    mcp_config = getattr(config, "mcp", None)
    if mcp_config is not None and not getattr(mcp_config, "enabled", True):
        return None

    try:
        from mcp_server.server import create_server  # lazy import

        return create_server(config=mcp_config)
    except ImportError:
        return None
