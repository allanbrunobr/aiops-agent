"""Setup do SlackNotifier — extraído de main.py (Story 4.1).

Encapsula configuração do SlackNotifier a partir de variáveis de ambiente
SLACK_BOT_TOKEN e SLACK_CHANNEL.
"""

from __future__ import annotations

import os


def setup_slack_notifier(config: object = None) -> object | None:
    """Cria e retorna um SlackNotifier se as env vars estiverem configuradas.

    Lazy-imports dependências de Slack para não aumentar custo de startup.
    Retorna None silenciosamente se token/canal não estiverem configurados.

    Args:
        config: Configuração da aplicação (opcional — reservado para uso futuro).

    Returns:
        SlackNotifier configurado, ou None se variáveis de ambiente ausentes.
    """
    slack_token = os.environ.get("SLACK_BOT_TOKEN")
    slack_channel = os.environ.get("SLACK_CHANNEL", "").lstrip("#")

    if not slack_token or not slack_channel:
        return None

    try:
        from server.notifications.slack import SlackNotifier  # lazy import
        from core.credentials import EnvCredentialProvider

        return SlackNotifier(
            channel=slack_channel,
            creds=EnvCredentialProvider(),
        )
    except Exception:
        return None
