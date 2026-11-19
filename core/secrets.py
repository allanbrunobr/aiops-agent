"""Sanitização de segredos em texto livre (Story 3.3).

Cross-cutting: usado pela CLI ao exibir erros e disponível a qualquer camada
que precise redigir API keys antes de logar/persistir. Sem dependências além
da stdlib — ``core/`` é type-checado pelo mypy.
"""

from __future__ import annotations

import re

# Fonte única de padrões de segredo (M4/F5-06): core/logging.py reexporta
# esta lista como SECRET_PATTERNS para a redação nos logs — antes eram
# duas listas divergentes (4 aqui vs 6 lá) e tokens Slack/Bearer passavam
# intactos pelo sanitizador da CLI.
API_KEY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"),  # Anthropic API keys
    re.compile(r"dapi[a-zA-Z0-9]{20,}"),  # Databricks API keys
    re.compile(r"sk-[a-zA-Z0-9]{32,}"),  # OpenAI-style keys
    re.compile(r"key-[a-zA-Z0-9]{32,}"),  # Generic API keys
    re.compile(r"xoxb-[a-zA-Z0-9-]+"),  # Slack Bot tokens
    re.compile(r"Bearer\s+[a-zA-Z0-9._-]{20,}"),  # Bearer tokens
]


def sanitize_secrets(text: str) -> str:
    """Remove padrões de API key do texto, substituindo por ``[REDACTED]``."""
    for pattern in API_KEY_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def contains_api_key_pattern(text: str) -> bool:
    """Verifica se o texto contém algum padrão de API key."""
    return any(pattern.search(text) for pattern in API_KEY_PATTERNS)


__all__ = ["API_KEY_PATTERNS", "sanitize_secrets", "contains_api_key_pattern"]
