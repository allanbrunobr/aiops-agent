"""Mapeamento de ferramentas MCP para scopes OAuth 2.1.

Cada ferramenta exige um scope específico. Agentes que não possuem o scope
recebem 403 com o scope necessário no body da resposta.
"""
from __future__ import annotations

import json

from mcp.types import TextContent

# Mapa ferramenta → scope obrigatório
TOOL_SCOPES: dict[str, str] = {
    "diagnose_incident": "aiops:diagnose",
    "search_incidents": "aiops:search",
    "validate_action": "aiops:validate",
}

# Todos os scopes suportados pelo sistema
ALL_SCOPES: list[str] = list(TOOL_SCOPES.values())


def require_scope(tool_name: str, granted_scopes: list[str]) -> TextContent | None:
    """Verifica se o agente possui o scope necessário para a ferramenta.

    Args:
        tool_name: Nome da ferramenta MCP sendo chamada.
        granted_scopes: Lista de scopes do JWT do agente.

    Returns:
        TextContent com erro 403 se scope insuficiente, None se autorizado.
    """
    required = TOOL_SCOPES.get(tool_name)
    if required is None:
        return None  # ferramenta sem restrição de scope

    if required not in granted_scopes:
        body = json.dumps({
            "type": "forbidden",
            "title": "Insufficient scope",
            "detail": f"Tool '{tool_name}' requires scope '{required}'.",
            "required_scope": required,
            "granted_scopes": granted_scopes,
        })
        return TextContent(type="text", text=body)

    return None
