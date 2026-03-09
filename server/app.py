from __future__ import annotations

import structlog

from blueprint.engine import BlueprintEngine
from config.loader import AppConfig, load_config
from core.credentials import EnvCredentialProvider

log = structlog.get_logger()


def create_engine(config: AppConfig | None = None) -> BlueprintEngine:
    """Cria e configura o BlueprintEngine com observers registrados.

    Composicao root: conecta config, credenciais e notificacoes.
    """
    if config is None:
        config = load_config()

    creds = EnvCredentialProvider()
    engine = BlueprintEngine()

    slack_config = config.notifications.slack

    if slack_config.enabled:
        from server.notifications.slack import SlackNotifier

        notifier = SlackNotifier(
            channel=slack_config.channel,
            creds=creds,
            max_retries=slack_config.max_retries,
            initial_backoff_s=slack_config.initial_backoff_s,
            backoff_multiplier=slack_config.backoff_multiplier,
            fallback_dir=slack_config.fallback_dir,
            dry_run=slack_config.dry_run,
        )
        engine.add_observer(notifier)
        log.info(
            "slack.observer_registered",
            channel=slack_config.channel,
            dry_run=slack_config.dry_run,
        )

    return engine
