"""Slack: erros permanentes não são retentados, 429 respeita Retry-After e
blocos respeitam os limites da API.

P2 — 4B-11 (docs/analise-20261109.md): só erros de autenticação paravam o
retry (channel_not_found, invalid_blocks, msg_too_long... eram retentados com
backoff); `ratelimited` ignorava o Retry-After; os blocos não eram truncados,
e uma mensagem final grande era recusada pela API.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from interfaces.http.notifications.slack import (
    MAX_BLOCKS,
    MAX_SECTION_TEXT,
    MAX_RETRY_AFTER_S,
)
from tests.test_notifications import _make_event, _make_notifier, _make_slack_error


def _rate_limited(retry_after: str) -> SlackApiError:
    response = MagicMock()
    response.get.side_effect = lambda key, default=None: "ratelimited" if key == "error" else default
    response.headers = {"Retry-After": retry_after}
    return SlackApiError("ratelimited", response=response)


def _run(coro):
    with patch("asyncio.sleep", new_callable=AsyncMock) as sleep:
        try:
            asyncio.run(coro)
        except SlackApiError:
            pass
    return sleep


@pytest.mark.unit
class TestPermanentErrors:
    @pytest.mark.parametrize("code", ["channel_not_found", "not_in_channel", "invalid_blocks",
                                      "msg_too_long", "is_archived", "missing_scope"])
    def test_permanent_error_is_not_retried(self, code, tmp_path):
        client = AsyncMock()
        client.chat_postMessage.side_effect = _make_slack_error(code)
        notifier = _make_notifier(client, fallback_dir=str(tmp_path))
        sleep = _run(notifier.on_event(_make_event("pipeline.started")))
        assert client.chat_postMessage.call_count == 1
        sleep.assert_not_called()


@pytest.mark.unit
class TestRateLimit:
    def test_retry_after_header_is_honoured(self, tmp_path):
        client = AsyncMock()
        client.chat_postMessage.side_effect = [_rate_limited("7"), {"ts": "1.1"}]
        notifier = _make_notifier(client, fallback_dir=str(tmp_path))
        sleep = _run(notifier.on_event(_make_event("pipeline.started")))
        sleep.assert_called_once_with(7.0)

    def test_retry_after_is_capped(self, tmp_path):
        client = AsyncMock()
        client.chat_postMessage.side_effect = [_rate_limited("3600"), {"ts": "1.1"}]
        notifier = _make_notifier(client, fallback_dir=str(tmp_path))
        sleep = _run(notifier.on_event(_make_event("pipeline.started")))
        sleep.assert_called_once_with(float(MAX_RETRY_AFTER_S))


def _texts(blocks):
    for block in blocks:
        text = block.get("text")
        if isinstance(text, dict):
            yield text.get("text", "")
        for field in block.get("fields", []) or []:
            yield field.get("text", "")


@pytest.mark.unit
class TestBlockLimits:
    def test_huge_final_message_fits_api_limits(self, tmp_path):
        client = AsyncMock()
        client.chat_postMessage.return_value = {"ts": "1.1", "channel": "C1"}
        client.chat_update.return_value = {"ts": "1.1"}
        notifier = _make_notifier(client, fallback_dir=str(tmp_path))
        data = {"root_cause": "x" * 10_000, "evidence": [f"evidence {i} " + "y" * 500 for i in range(200)],
                "proposed_fix": "kubectl rollout undo deployment/api", "confidence": 0.9}
        _run(notifier.on_event(_make_event("pipeline.started")))
        _run(notifier.on_event(_make_event("pipeline.completed", data=data)))

        sent = [c.kwargs["blocks"] for c in client.chat_update.call_args_list + client.chat_postMessage.call_args_list
                if c.kwargs.get("blocks")]
        assert sent, "nenhuma mensagem com blocos foi enviada"
        for blocks in sent:
            assert len(blocks) <= MAX_BLOCKS
            assert all(len(t) <= MAX_SECTION_TEXT for t in _texts(blocks))
