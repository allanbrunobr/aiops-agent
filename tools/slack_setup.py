"""Setup do SlackNotifier — composition helper da CLI.

Credenciais vêm do ambiente (``SLACK_BOT_TOKEN``, ``SLACK_CHANNEL``); os
parâmetros operacionais vêm de ``config.slack`` (F5-3). Antes, o parâmetro
``config`` era aceito e ignorado — retries/backoff/fallback_dir/dry_run ficavam
nos defaults do construtor e divergiam silenciosamente do YAML.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from config.loader import AppConfig
    from interfaces.http.notifications.slack import SlackNotifier

log = structlog.get_logger()


def setup_slack_notifier(config: AppConfig | None = None) -> SlackNotifier | None:
    """Cria um SlackNotifier se token e canal estiverem disponíveis.

    Canal: ``config.slack.channel`` tem precedência; fallback ``SLACK_CHANNEL``.
    Token: sempre do ambiente (``SLACK_BOT_TOKEN``), via EnvCredentialProvider —
    nunca do YAML. Slack é opcional: sem token ou canal retorna None, sem lançar.
    ``config.slack.enabled`` NÃO é consultado aqui — a presença de token+canal é
    o opt-in da CLI (contrato documentado no README).
    """
    slack_cfg = config.slack if config is not None else None
    channel = (slack_cfg.channel if slack_cfg is not None else "") or os.environ.get(
        "SLACK_CHANNEL", ""
    )
    channel = channel.lstrip("#")
    if not os.environ.get("SLACK_BOT_TOKEN") or not channel:
        return None

    try:
        from adapters.credentials import EnvCredentialProvider
        from interfaces.http.notifications.slack import SlackNotifier  # lazy import

        kwargs: dict = {}
        if slack_cfg is not None:
            kwargs = {
                "max_retries": slack_cfg.max_retries,
                "initial_backoff_s": slack_cfg.initial_backoff_s,
                "backoff_multiplier": slack_cfg.backoff_multiplier,
                "fallback_dir": slack_cfg.fallback_dir,
                "dry_run": slack_cfg.dry_run,
            }
        return SlackNotifier(channel=channel, creds=EnvCredentialProvider(), **kwargs)
    except Exception as exc:  # Slack é opcional: não derruba a CLI
        log.warning("slack.notifier_init_failed", error=str(exc))
        return None
