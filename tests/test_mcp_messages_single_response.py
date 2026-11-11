"""`POST /mcp/messages` responde uma vez só.

O `SseServerTransport.handle_post_message` envia a resposta HTTP pelo `send`
do ASGI. O endpoint FastAPI devolvia `None`, e o FastAPI tentava enviar uma
segunda resposta (`null`) depois da primeira (pendência anotada na P2).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def _client() -> TestClient:
    from interfaces.mcp import sse

    app = FastAPI()
    oauth = SimpleNamespace(jwt_secret="", trusted_issuers=[], issuer="", audience="",
                            allowed_clients=[{"id": "assistant-agent", "scopes": ["aiops:search"]}])
    sse.mount_sse(app, config=SimpleNamespace(auth_method="header", oauth=oauth, enabled_tools=[]))
    return TestClient(app)


def test_transport_reply_is_the_only_reply():
    """Sessão inexistente: o transporte responde (4xx) e nada mais é enviado."""
    resp = _client().post(
        "/mcp/messages?session_id=00000000000000000000000000000000",
        content=b"{}", headers={"X-Client-Id": "assistant-agent",
                                "Content-Type": "application/json"},
    )
    assert 400 <= resp.status_code < 500
    assert resp.text != "null"
