"""F5-3: setup_slack_notifier passa config.slack adiante.

Antes, o parâmetro ``config`` era "reservado para uso futuro" e ignorado:
max_retries/backoff/fallback_dir/dry_run do YAML nunca chegavam ao
SlackNotifier. Token continua vindo só do ambiente.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from config.loader import AppConfig, SlackConfig
from tools.slack_setup import setup_slack_notifier


def _config(**slack) -> AppConfig:
    return AppConfig(slack=SlackConfig(**slack))


@pytest.fixture
def notifier_cls():
    with patch("interfaces.http.notifications.slack.SlackNotifier") as cls:
        cls.return_value = MagicMock(name="notifier")
        yield cls


@pytest.mark.unit
def test_without_token_returns_none(monkeypatch, notifier_cls):
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("SLACK_CHANNEL", "#ops")
    assert setup_slack_notifier(_config(channel="ops")) is None
    notifier_cls.assert_not_called()


@pytest.mark.unit
def test_without_channel_anywhere_returns_none(monkeypatch, notifier_cls):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.delenv("SLACK_CHANNEL", raising=False)
    assert setup_slack_notifier(_config(channel="")) is None
    notifier_cls.assert_not_called()


@pytest.mark.unit
def test_config_slack_values_are_forwarded(monkeypatch, notifier_cls):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.delenv("SLACK_CHANNEL", raising=False)
    cfg = _config(
        channel="#aiops-alerts",
        max_retries=7,
        initial_backoff_s=0.25,
        backoff_multiplier=3.0,
        fallback_dir="out/slack",
        dry_run=True,
    )

    assert setup_slack_notifier(cfg) is notifier_cls.return_value

    kwargs = notifier_cls.call_args.kwargs
    assert kwargs["channel"] == "aiops-alerts"  # '#' removido
    assert kwargs["max_retries"] == 7
    assert kwargs["initial_backoff_s"] == 0.25
    assert kwargs["backoff_multiplier"] == 3.0
    assert kwargs["fallback_dir"] == "out/slack"
    assert kwargs["dry_run"] is True


@pytest.mark.unit
def test_env_channel_is_fallback_when_config_channel_empty(monkeypatch, notifier_cls):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("SLACK_CHANNEL", "#from-env")

    setup_slack_notifier(_config(channel=""))

    assert notifier_cls.call_args.kwargs["channel"] == "from-env"


@pytest.mark.unit
def test_config_channel_has_precedence_over_env(monkeypatch, notifier_cls):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("SLACK_CHANNEL", "#from-env")

    setup_slack_notifier(_config(channel="from-config"))

    assert notifier_cls.call_args.kwargs["channel"] == "from-config"


@pytest.mark.unit
def test_without_config_uses_constructor_defaults(monkeypatch, notifier_cls):
    """Compat: chamada antiga setup_slack_notifier() sem config segue funcionando."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("SLACK_CHANNEL", "ops")

    setup_slack_notifier()

    kwargs = notifier_cls.call_args.kwargs
    assert kwargs["channel"] == "ops"
    assert "max_retries" not in kwargs and "dry_run" not in kwargs


@pytest.mark.unit
def test_init_failure_is_swallowed_and_logged(monkeypatch, notifier_cls):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-x")
    monkeypatch.setenv("SLACK_CHANNEL", "ops")
    notifier_cls.side_effect = RuntimeError("boom")

    with patch("tools.slack_setup.log") as log:
        assert setup_slack_notifier(_config(channel="ops")) is None
    log.warning.assert_called_once()
    assert log.warning.call_args.args[0] == "slack.notifier_init_failed"
