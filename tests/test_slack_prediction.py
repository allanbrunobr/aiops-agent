"""
Testes unitários para _build_prediction_blocks e handle_prediction_event.

Verifica blocos Block Kit para eventos de predição pré-incidente:
prediction.warning (risk < 60) e prediction.alert (risk >= 60).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from interfaces.http.notifications.slack import SlackNotifier
from interfaces.http.app import handle_prediction_event


def _make_notifier(has_token: bool = True) -> SlackNotifier:
    """Cria SlackNotifier com ou sem token para testes."""
    creds = MagicMock()
    creds.get.return_value = "xoxb-fake-token" if has_token else None

    with patch("interfaces.http.notifications.slack.AsyncWebClient"):
        notifier = SlackNotifier(channel="#ops", creds=creds)
        if not has_token:
            notifier._client = None
    return notifier


# ---- _build_prediction_blocks ----

def test_warning_level_header(tmp_path):
    """prediction.warning deve gerar header com emoji 📈 e risk score."""
    notifier = _make_notifier()
    data = {
        "event_level": "prediction.warning",
        "risk_score": 45.0,
        "eta_minutes": None,
        "signals": [],
        "matched_history": [],
    }

    blocks = notifier._build_prediction_blocks(data)

    header = blocks[0]
    assert header["type"] == "header"
    assert "📈" in header["text"]["text"]
    assert "45" in header["text"]["text"]


def test_alert_level_header():
    """prediction.alert deve gerar header com emoji ⚠️ e ETA."""
    notifier = _make_notifier()
    data = {
        "event_level": "prediction.alert",
        "risk_score": 75.0,
        "eta_minutes": 12.5,
        "signals": [],
        "matched_history": [],
    }

    blocks = notifier._build_prediction_blocks(data)

    header = blocks[0]
    assert "⚠️" in header["text"]["text"]
    assert "12.5" in header["text"]["text"]


def test_none_eta_shows_unknown():
    """ETA None deve exibir 'Unknown' na mensagem."""
    notifier = _make_notifier()
    data = {
        "event_level": "prediction.warning",
        "risk_score": 35.0,
        "eta_minutes": None,
        "signals": [],
        "matched_history": [],
    }

    blocks = notifier._build_prediction_blocks(data)

    # Busca bloco de ETA
    eta_block = next(
        b for b in blocks
        if b.get("type") == "section" and "ETA" in b["text"]["text"]
    )
    assert "Unknown" in eta_block["text"]["text"]


def test_signals_listed_in_blocks():
    """Nomes dos sinais devem aparecer nos blocos."""
    class _FakeSignal:
        pass

    notifier = _make_notifier()
    data = {
        "event_level": "prediction.warning",
        "risk_score": 40.0,
        "eta_minutes": 5.0,
        "signals": [_FakeSignal(), _FakeSignal()],
        "matched_history": [],
    }

    blocks = notifier._build_prediction_blocks(data)
    signals_block = next(
        b for b in blocks
        if b.get("type") == "section" and "Signals" in b["text"]["text"]
    )
    assert "_FakeSignal" in signals_block["text"]["text"]


def test_no_history_shows_fallback():
    """Sem histórico correspondente, deve exibir 'No historical match'."""
    notifier = _make_notifier()
    data = {
        "event_level": "prediction.warning",
        "risk_score": 30.0,
        "eta_minutes": None,
        "signals": [],
        "matched_history": [],
    }

    blocks = notifier._build_prediction_blocks(data)
    history_block = next(
        b for b in blocks
        if b.get("type") == "section" and "Historical" in b["text"]["text"]
    )
    assert "No historical match" in history_block["text"]["text"]


def test_with_history_shows_incident_id():
    """Com histórico, deve exibir incident_id da primeira correspondência."""
    notifier = _make_notifier()
    data = {
        "event_level": "prediction.alert",
        "risk_score": 80.0,
        "eta_minutes": 3.0,
        "signals": [],
        "matched_history": [
            {"incident_id": "inc-42", "similarity_score": 0.87, "outcome": "pre_incident risk=75.0"}
        ],
    }

    blocks = notifier._build_prediction_blocks(data)
    history_block = next(
        b for b in blocks
        if b.get("type") == "section" and "Historical" in b["text"]["text"]
    )
    assert "inc-42" in history_block["text"]["text"]


# ---- no Slack token ----

@pytest.mark.asyncio
async def test_no_token_no_exception():
    """Sem token Slack configurado, handle_prediction_event não deve lançar exceção."""
    notifier = _make_notifier(has_token=False)
    data = {
        "event_level": "prediction.warning",
        "risk_score": 45.0,
        "eta_minutes": None,
        "signals": [],
        "matched_history": [],
        "incident_id": "inc-no-token",
    }

    try:
        await handle_prediction_event("prediction.warning", data, notifier)
    except Exception as exc:
        pytest.fail(f"handle_prediction_event lançou exceção sem token: {exc}")


# ---- handle_prediction_event ----

@pytest.mark.asyncio
async def test_handle_warning_sends_message():
    """prediction.warning deve chamar chat_postMessage com color amarelo."""
    notifier = _make_notifier(has_token=True)
    notifier._client = AsyncMock()
    notifier._client.chat_postMessage = AsyncMock(return_value={"ok": True})

    data = {
        "event_level": "prediction.warning",
        "risk_score": 50.0,
        "eta_minutes": 10.0,
        "signals": [],
        "matched_history": [],
        "incident_id": "inc-warn",
    }

    await handle_prediction_event("prediction.warning", data, notifier)

    notifier._client.chat_postMessage.assert_called_once()
    call_kwargs = notifier._client.chat_postMessage.call_args.kwargs
    attachment = call_kwargs["attachments"][0]
    assert attachment["color"] == "#ffcc00"


@pytest.mark.asyncio
async def test_handle_alert_sends_red_color():
    """prediction.alert deve chamar chat_postMessage com color vermelho."""
    notifier = _make_notifier(has_token=True)
    notifier._client = AsyncMock()
    notifier._client.chat_postMessage = AsyncMock(return_value={"ok": True})

    data = {
        "event_level": "prediction.alert",
        "risk_score": 80.0,
        "eta_minutes": 5.0,
        "signals": [],
        "matched_history": [],
        "incident_id": "inc-alert",
    }

    await handle_prediction_event("prediction.alert", data, notifier)

    call_kwargs = notifier._client.chat_postMessage.call_args.kwargs
    attachment = call_kwargs["attachments"][0]
    assert attachment["color"] == "#ff0000"


@pytest.mark.asyncio
async def test_handle_exception_does_not_propagate():
    """Exceção no Slack não deve propagar ao caller."""
    notifier = _make_notifier(has_token=True)
    notifier._client = AsyncMock()
    notifier._client.chat_postMessage = AsyncMock(side_effect=RuntimeError("timeout"))

    data = {
        "event_level": "prediction.warning",
        "risk_score": 40.0,
        "eta_minutes": None,
        "signals": [],
        "matched_history": [],
        "incident_id": "inc-exc",
    }

    try:
        await handle_prediction_event("prediction.warning", data, notifier)
    except Exception as exc:
        pytest.fail(f"handle_prediction_event propagou exceção: {exc}")
