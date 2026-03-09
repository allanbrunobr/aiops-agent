"""Configuracao de logging estruturado com structlog.

Configura structlog como backend de logging com output JSON, redacao
de secrets, e context binding para correlacao de pipeline/node.
"""

from __future__ import annotations

import logging
import os
import re
import sys

import structlog


# Padroes de secrets para redacao — reutilizados de main.py
_SECRET_PATTERNS = [
    re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"),      # Anthropic API keys
    re.compile(r"dapi[a-zA-Z0-9]{20,}"),             # Databricks API keys
    re.compile(r"sk-[a-zA-Z0-9]{32,}"),              # OpenAI-style keys
    re.compile(r"key-[a-zA-Z0-9]{32,}"),             # Generic API keys
    re.compile(r"xoxb-[a-zA-Z0-9-]+"),               # Slack Bot tokens
    re.compile(r"Bearer\s+[a-zA-Z0-9._-]{20,}"),     # Bearer tokens
]

# Chaves cujo valor SEMPRE deve ser redactado
_SENSITIVE_KEYS = frozenset({
    "password", "secret", "token", "api_key", "apikey",
    "credential", "credentials", "authorization", "auth_token",
    "private_key", "access_token", "refresh_token",
})


def _redact_secrets(
    logger: logging.Logger,
    method_name: str,
    event_dict: dict,
) -> dict:
    """Processador structlog que redacta secrets de todos os valores do log."""
    for key, value in event_dict.items():
        if isinstance(value, str):
            # Redacta por chave sensivel
            if key.lower() in _SENSITIVE_KEYS:
                event_dict[key] = "[REDACTED]"
                continue
            # Redacta por padrao de secret no valor
            for pattern in _SECRET_PATTERNS:
                value = pattern.sub("[REDACTED]", value)
            event_dict[key] = value
    return event_dict


def configure_logging(log_level: str | None = None) -> None:
    """Configura structlog como backend de logging.

    Deve ser chamada UMA VEZ na inicializacao da aplicacao (composition root).
    Configura tanto structlog quanto stdlib logging para output JSON unificado.

    Args:
        log_level: Nivel de log (DEBUG, INFO, WARNING, ERROR). Se None,
                   usa AIOPS_LOG_LEVEL do ambiente ou INFO como default.
    """
    level_name = log_level or os.environ.get("AIOPS_LOG_LEVEL", "INFO")
    level = getattr(logging, level_name.upper(), logging.INFO)

    # Processadores compartilhados entre structlog e stdlib
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        _redact_secrets,
    ]

    # Configura structlog
    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Configura stdlib logging para rotear pela mesma cadeia
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Reduz ruido de libs terceiras
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Retorna logger structlog nomeado para um dominio.

    O nome deve seguir convencao <domain> — e.g., 'pipeline', 'safety',
    'blueprint', 'webhook', 'mcp'. Os eventos devem ser <domain>.<action>.

    Args:
        name: Nome do dominio (usado como logger name).

    Returns:
        Logger structlog com bind disponivel.
    """
    return structlog.get_logger(name)


def bind_context(
    *,
    correlation_id: str | None = None,
    pipeline: str | None = None,
    node: str | None = None,
) -> structlog.stdlib.BoundLogger:
    """Cria logger com contexto de pipeline/node vinculado.

    Uso tipico: chamado no inicio de cada BlueprintNode.execute() para
    criar um logger com contexto completo de rastreamento.

    Args:
        correlation_id: ID de correlacao do incidente (inc-YYYYMMDD-uuid8).
        pipeline: Nome do pipeline (e.g., 'incident_response').
        node: Nome do node (e.g., 'log_parser', 'diagnoser').

    Returns:
        Logger com campos vinculados. Todos os logs emitidos por este
        logger incluirao os campos fornecidos automaticamente.
    """
    log = structlog.get_logger("blueprint")
    bindings: dict[str, str] = {}
    if correlation_id is not None:
        bindings["correlation_id"] = correlation_id
    if pipeline is not None:
        bindings["pipeline"] = pipeline
    if node is not None:
        bindings["node"] = node
    return log.bind(**bindings)
