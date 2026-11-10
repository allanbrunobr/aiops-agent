"""A fixture `isolated_cli` impede que testes de main()/main_v1() postem no Slack.

Os entrypoints montam um SlackNotifier real quando há SLACK_BOT_TOKEN no
ambiente — inclusive vindo do `.env.local` do checkout. O teste usa um token
gerado na hora e confere que o import feito pelos entrypoints recebe None.
"""
from __future__ import annotations

import os
import secrets

import pytest


@pytest.mark.unit
def test_isolated_cli_disables_slack_even_with_token(isolated_cli):
    os.environ["SLACK_BOT_TOKEN"] = "xoxb-" + secrets.token_hex(8)
    os.environ["SLACK_CHANNEL"] = "#should-not-be-used"

    # Mesmo caminho de import que main() e main_v1() usam, em tempo de chamada.
    from tools.slack_setup import setup_slack_notifier

    assert setup_slack_notifier() is None


@pytest.mark.unit
def test_isolated_cli_clears_credential_variables(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-" + secrets.token_hex(12))
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-" + secrets.token_hex(8))

    # Usa a fixture como gerador para inspecionar o ambiente dentro e depois.
    from tests.conftest import isolated_cli as fixture

    gen = fixture.__wrapped__(monkeypatch)
    next(gen)
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert "SLACK_BOT_TOKEN" not in os.environ
    with pytest.raises(StopIteration):
        next(gen)
    assert "ANTHROPIC_API_KEY" in os.environ  # restaurado ao sair


@pytest.mark.unit
def test_slack_is_disabled_even_without_requesting_the_fixture(monkeypatch):
    """Achado da revisão pré-PR: testes de main() em test_api_key_safety.py não
    pediam `isolated_cli` e podiam postar no Slack real num checkout com
    `.env.local`. O isolamento passa a ser automático para toda a suíte."""
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-" + secrets.token_hex(8))
    monkeypatch.setenv("SLACK_CHANNEL", "#should-not-be-used")

    from tools.slack_setup import setup_slack_notifier

    assert setup_slack_notifier() is None
