"""`mcp.auth_method` desconhecido falha fechado (S4/SEC-03).

P2 (docs/analise-20261109.md): auth_method era string livre e qualquer valor
fora de "oauth21"/"header" (ex.: um typo como "oauth2.1") caía em
"anonymous" sem checagem de scope — o MCP ficava aberto.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError


@pytest.mark.unit
class TestConfigValidation:
    @pytest.mark.parametrize("value", ["oauth2.1", "OAUTH21", "", "anonymous"])
    def test_unknown_auth_method_is_rejected_at_load(self, value):
        from config.loader import AppConfig

        with pytest.raises(ValidationError):
            AppConfig(mcp={"auth_method": value})

    @pytest.mark.parametrize("value", ["header", "oauth21", "federated", "none"])
    def test_known_auth_methods_are_accepted(self, value):
        from config.loader import AppConfig

        assert AppConfig(mcp={"auth_method": value}).mcp.auth_method == value


class _PassedAuth(Exception):
    """Levantada pelo transporte falso: a autenticação deixou o pedido passar."""


class _FakeTransport:
    def __init__(self, *_args, **_kwargs):
        pass

    async def handle_post_message(self, *_args, **_kwargs):
        raise _PassedAuth


def _client(auth_method: str, monkeypatch) -> TestClient:
    from interfaces.mcp import sse

    monkeypatch.setattr(sse, "SseServerTransport", _FakeTransport)
    app = FastAPI()
    config = SimpleNamespace(auth_method=auth_method, oauth=None, enabled_tools=[])
    sse.mount_sse(app, config=config)
    return TestClient(app)


@pytest.mark.unit
class TestSseFailsClosed:
    def test_unknown_method_returns_401_on_messages(self, monkeypatch):
        """Defesa em profundidade para configs que não passam pelo AppConfig."""
        resp = _client("oauth2.1", monkeypatch).post("/mcp/messages", content=b"{}")
        assert resp.status_code == 401

    def test_explicit_none_is_still_anonymous(self, monkeypatch):
        with pytest.raises(_PassedAuth):
            _client("none", monkeypatch).post("/mcp/messages", content=b"{}")
