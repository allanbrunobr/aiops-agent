"""
MCP Server Factory para o AIOps Agent.

Define o servidor MCP com três ferramentas: diagnose_incident, validate_action,
search_incidents. Suporta transporte stdio (dev local) e SSE (produção).

Referência: architecture.md — MCP Transport Decision, Lines 334-341.
História 5.1: Fundação do servidor MCP com transporte dual.
"""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar

import structlog
from mcp.server import Server
from mcp.types import TextContent, Tool

log = structlog.get_logger()

# ContextVars para propagar client_id e scopes ao longo do ciclo de vida do tool call
_current_client_id: ContextVar[str] = ContextVar("client_id", default="unknown")
_current_scopes: ContextVar[list[str]] = ContextVar("scopes", default=[])


def _get_current_client_id() -> str:
    """Retorna client_id do contexto atual (injetado pelo transporte)."""
    return _current_client_id.get()


def create_server(
    name: str = "aiops-agent",
    rate_limiter: object | None = None,
    config: object | None = None,
    retriever: object | None = None,
    sanitizer: object | None = None,
    pre_validator: object | None = None,
    tnr: object | None = None,
    version_validator: object | None = None,
    engine: object | None = None,
    normalizer_registry: object | None = None,
    incident_store: object | None = None,
) -> Server:
    """Cria instância do servidor MCP com definições de ferramentas.

    Todas as dependências injetadas via parâmetros (sem imports globais).
    Rate limiter verificado antes de despachar qualquer tool call.

    Args:
        name: Nome do servidor MCP.
        rate_limiter: RateLimiter para throttling por cliente.
        config: McpConfig para filtrar ferramentas habilitadas.
        retriever: RAG retriever para search_incidents.
        sanitizer: TelemetrySanitizer para PII check (validate_action + search).
        pre_validator: PreExecutionValidator (camada 2 do validate_action).
        tnr: TNRSafetyGuard (camada 3 do validate_action).
        version_validator: VersionValidator (camada 4 do validate_action).
        engine: BlueprintEngine para diagnose_incident.
        normalizer_registry: Registro de normalizadores para diagnose_incident.
        incident_store: IncidentStore para rastreamento de incidentes.

    Returns:
        Server MCP configurado e pronto para uso.
    """
    server = Server(name)

    # Conjunto de ferramentas habilitadas (todas por padrão)
    enabled_tools: set[str] = {
        "diagnose_incident",
        "validate_action",
        "search_incidents",
    }
    if config is not None:
        configured_tools = getattr(config, "enabled_tools", None)
        if configured_tools is not None:
            enabled_tools = set(configured_tools)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """Retorna lista de ferramentas disponíveis no MCP server."""
        all_tools = _build_tool_definitions()
        return [t for t in all_tools if t.name in enabled_tools]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        """Despacha chamadas de ferramentas com rate limiting e logging.

        Verifica rate limit ANTES do despacho para qualquer ferramenta.
        Ferramentas desabilitadas retornam erro imediatamente.
        """
        if name not in enabled_tools:
            error_body = json.dumps(
                {
                    "type": "tool_disabled",
                    "title": "Tool not available",
                    "detail": f"Tool '{name}' is not enabled in current configuration.",
                }
            )
            return [TextContent(type="text", text=error_body)]

        client_id = _get_current_client_id()

        # Scope enforcement — obrigatório quando auth_method=oauth21
        granted_scopes = _current_scopes.get()
        if granted_scopes:  # lista vazia = modo legado sem enforcement
            from auth.scopes import require_scope
            scope_error = require_scope(name, granted_scopes)
            if scope_error is not None:
                return [scope_error]

        # Rate limit check — antes de qualquer lógica de negócio
        if rate_limiter is not None:
            result = rate_limiter.check(client_id, name)
            if not result.allowed:
                log.warning(
                    "mcp.rate_limit.exceeded",
                    client_id=client_id,
                    tool=name,
                    retry_after=result.retry_after,
                )
                error_body = json.dumps(
                    {
                        "type": "rate_limit_exceeded",
                        "title": "Rate limit exceeded",
                        "detail": f"Rate limit exceeded for {name}. Retry after {result.retry_after}s",
                        "retry_after": result.retry_after,
                        "limit": result.limit,
                        "tool": name,
                        "client_id": client_id,
                    }
                )
                return [TextContent(type="text", text=error_body)]
            log.info(
                "mcp.rate_limit.allowed",
                client_id=client_id,
                tool=name,
                remaining=result.remaining,
            )

        # Despacho para handler concreto
        response_text = await _dispatch(
            name=name,
            arguments=arguments,
            client_id=client_id,
            retriever=retriever,
            sanitizer=sanitizer,
            pre_validator=pre_validator,
            tnr=tnr,
            version_validator=version_validator,
            engine=engine,
            normalizer_registry=normalizer_registry,
            incident_store=incident_store,
            config=config,
        )
        return [TextContent(type="text", text=response_text)]

    return server


async def _dispatch(
    name: str,
    arguments: dict,
    client_id: str,
    **deps: object,
) -> str:
    """Despacha chamada para o handler correto.

    Args:
        name: Nome da ferramenta.
        arguments: Argumentos da chamada.
        client_id: Identificador do cliente.
        **deps: Dependências injetadas (retriever, sanitizer, etc.).

    Returns:
        JSON string com resultado ou erro.
    """
    from interfaces.mcp.tools import (
        handle_diagnose_incident,
        handle_search_incidents,
        handle_validate_action,
    )

    if name == "search_incidents":
        return await handle_search_incidents(
            arguments=arguments,
            retriever=deps.get("retriever"),
            sanitizer=deps.get("sanitizer"),
        )
    elif name == "validate_action":
        pre_validator = deps.get("pre_validator")
        tnr = deps.get("tnr")
        version_validator = deps.get("version_validator")
        sanitizer = deps.get("sanitizer")

        if any(d is None for d in [sanitizer, pre_validator, tnr, version_validator]):
            return json.dumps(
                {
                    "type": "dependencies_unavailable",
                    "title": "Safety layers not initialized",
                    "detail": "One or more safety layer dependencies are not available.",
                }
            )

        return await handle_validate_action(
            arguments=arguments,
            sanitizer=sanitizer,
            pre_validator=pre_validator,
            tnr=tnr,
            version_validator=version_validator,
            client_id=client_id,
        )
    elif name == "diagnose_incident":
        return await handle_diagnose_incident(
            arguments=arguments,
            engine=deps.get("engine"),
            normalizer_registry=deps.get("normalizer_registry"),
            incident_store=deps.get("incident_store"),
            config=deps.get("config"),
            client_id=client_id,
        )
    else:
        return json.dumps(
            {
                "type": "unknown_tool",
                "title": "Unknown tool",
                "detail": f"Tool '{name}' is not recognized.",
            }
        )


def _build_tool_definitions() -> list[Tool]:
    """Constrói lista completa de definições de ferramentas MCP."""
    return [
        Tool(
            name="diagnose_incident",
            description="Run full diagnostic pipeline on an incident alert",
            inputSchema={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Alert source (pagerduty, grafana, databricks)",
                    },
                    "severity": {
                        "type": "string",
                        "enum": ["critical", "high", "medium", "low"],
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "service": {
                        "type": "string",
                        "description": "Affected service name (optional)",
                    },
                    "telemetry_refs": {
                        "type": "object",
                        "description": "Optional references to log/metric/trace data",
                    },
                    "correlation_id": {
                        "type": "string",
                        "description": "Client-provided ID for traceability (optional)",
                    },
                },
                "required": ["source", "severity", "title", "description"],
            },
        ),
        Tool(
            name="validate_action",
            description="Validate a proposed remediation action through all safety layers",
            inputSchema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Remediation command to validate",
                    },
                    "target_service": {"type": "string"},
                    "blast_radius": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "Estimated number of services affected (default: 1)",
                    },
                },
                "required": ["command", "target_service"],
            },
        ),
        Tool(
            name="search_incidents",
            description="Search past incidents in the RAG knowledge base",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query text (semantic search)",
                    },
                    "service": {
                        "type": "string",
                        "description": "Filter by service name (optional)",
                    },
                    "top_k": {
                        "type": "integer",
                        "default": 5,
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Number of results to return",
                    },
                    "time_range": {
                        "type": "object",
                        "properties": {
                            "start": {"type": "string", "format": "date-time"},
                            "end": {"type": "string", "format": "date-time"},
                        },
                        "description": "ISO 8601 time range filter (optional)",
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter by tags (optional, matches any)",
                    },
                },
                "required": ["query"],
            },
        ),
    ]


# ============================================================================
# Transporte stdio (dev local / Claude Desktop)
# ============================================================================


async def run_stdio() -> None:
    """Executa servidor MCP via transporte stdio (dev local, Claude Desktop)."""
    from mcp.server.stdio import stdio_server

    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(run_stdio())
