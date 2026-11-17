"""F5-3: config/defaults.yaml e os modelos Pydantic não divergem mais.

- ``slack.webhook_url`` (campo fantasma, descartado por extra="ignore") saiu;
  o bloco ``slack`` do YAML espelha ``SlackConfig``.
- ``webhook.max_payload_bytes`` / ``dedup_window_seconds`` /
  ``max_concurrent_pipelines`` / ``max_queued_alerts`` existem no modelo e no
  YAML — antes o router usava ``getattr(..., default)`` que SEMPRE caía no
  fallback, e o server hardcodava 3/10.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from config.loader import AppConfig, RagConfig, SlackConfig, WebhookConfig, load_config

_DEFAULTS = Path(__file__).resolve().parent.parent / "config" / "defaults.yaml"


def _yaml() -> dict:
    return yaml.safe_load(_DEFAULTS.read_text(encoding="utf-8"))


@pytest.mark.unit
def test_yaml_sections_have_no_unknown_keys():
    """Toda chave do YAML em slack/webhook existe no modelo (sem campo fantasma)."""
    data = _yaml()
    assert set(data["slack"]) <= set(SlackConfig.model_fields)
    assert set(data["webhook"]) <= set(WebhookConfig.model_fields)
    assert set(data["rag"]) <= set(RagConfig.model_fields)
    assert "webhook_url" not in data["slack"]


@pytest.mark.unit
def test_webhook_operational_fields_come_from_yaml():
    cfg = load_config()
    assert cfg.webhook.max_payload_bytes == 1_048_576
    assert cfg.webhook.dedup_window_seconds == 300
    assert cfg.webhook.max_concurrent_pipelines == 3
    assert cfg.webhook.max_queued_alerts == 10


@pytest.mark.unit
def test_webhook_fields_override_via_env(monkeypatch):
    monkeypatch.setenv("AIOPS_WEBHOOK__DEDUP_WINDOW_SECONDS", "60")
    monkeypatch.setenv("AIOPS_WEBHOOK__MAX_PAYLOAD_BYTES", "2048")
    cfg = load_config()
    assert cfg.webhook.dedup_window_seconds == 60
    assert cfg.webhook.max_payload_bytes == 2048


@pytest.mark.unit
@pytest.mark.parametrize(
    "field, bad",
    [
        ("max_payload_bytes", 0),
        ("max_concurrent_pipelines", 0),
        ("dedup_window_seconds", -1),
        ("max_queued_alerts", -1),
    ],
)
def test_webhook_fields_are_validated(field, bad):
    with pytest.raises(ValidationError):
        WebhookConfig(**{field: bad})


@pytest.mark.unit
def test_slack_block_mirrors_model_defaults():
    cfg = AppConfig()
    assert cfg.slack.enabled is False
    assert cfg.slack.channel == ""
    assert cfg.slack.max_retries == 3
    assert cfg.slack.initial_backoff_s == 1.0
    assert cfg.slack.backoff_multiplier == 2.0
    assert cfg.slack.fallback_dir == "output"
    assert cfg.slack.dry_run is False
