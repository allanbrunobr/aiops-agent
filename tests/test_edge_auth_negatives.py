"""Caminhos negativos de autenticação na borda (T-d, item 4 do P2).

O relatório (docs/analise-20261109.md) apontou que faltavam testes de:
- /a2a/tasks com token ausente, inválido, expirado, sem segredo configurado
  e corpo acima de 1 MiB;
- SSE do MCP com X-Client-Id fora da allow-list e POST /mcp/messages.
São testes de cobertura sobre o comportamento atual; os segredos são gerados
em tempo de execução.
"""
from __future__ import annotations

import secrets
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt_validator import issue_token
from interfaces.http.routes.a2a import router as a2a_router

KEY = secrets.token_urlsafe(48)
BODY = {
    "jsonrpc": "2.0", "id": "1", "method": "tasks/send",
    "params": {"id": "t", "skill": {"id": "search_incidents"},
               "message": {"role": "user", "parts": [{"type": "data", "data": {"query": "x"}}]}},
}


def _a2a(secret: str = KEY) -> TestClient:
    app = FastAPI()
    app.include_router(a2a_router)
    app.state.a2a_jwt_secret = secret
    return TestClient(app)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.unit
class TestA2ANegatives:
    def test_missing_token_is_401(self):
        assert _a2a().post("/a2a/tasks", json=BODY).status_code == 401

    def test_garbage_token_is_401(self):
        assert _a2a().post("/a2a/tasks", json=BODY, headers=_bearer("not.a.jwt")).status_code == 401

    def test_token_signed_with_other_key_is_401(self):
        other = issue_token("assistant-agent", ["aiops:search"], secrets.token_urlsafe(48))
        assert _a2a().post("/a2a/tasks", json=BODY, headers=_bearer(other)).status_code == 401

    def test_expired_token_is_401(self):
        expired = issue_token("assistant-agent", ["aiops:search"], KEY, ttl=-10)
        resp = _a2a().post("/a2a/tasks", json=BODY, headers=_bearer(expired))
        assert resp.status_code == 401
        assert "expired" in resp.json()["detail"].lower()

    def test_unconfigured_secret_rejects_even_a_valid_looking_token(self):
        token = issue_token("assistant-agent", ["aiops:search"], KEY)
        assert _a2a(secret="").post("/a2a/tasks", json=BODY, headers=_bearer(token)).status_code == 401

    def test_body_over_one_mib_is_413(self):
        token = issue_token("assistant-agent", ["aiops:search"], KEY)
        big = b'{"pad": "' + b"x" * (1_048_576 + 10) + b'"}'
        resp = _a2a().post("/a2a/tasks", content=big,
                           headers={**_bearer(token), "Content-Type": "application/json"})
        assert resp.status_code == 413


class _PassedAuth(Exception):
    pass


class _FakeTransport:
    def __init__(self, *_a, **_k):
        pass

    async def handle_post_message(self, *_a, **_k):
        raise _PassedAuth


def _sse(monkeypatch, clients) -> TestClient:
    from interfaces.mcp import sse

    monkeypatch.setattr(sse, "SseServerTransport", _FakeTransport)
    app = FastAPI()
    oauth = SimpleNamespace(jwt_secret="", allowed_clients=clients, trusted_issuers=[],
                            issuer="", audience="")
    sse.mount_sse(app, config=SimpleNamespace(auth_method="header", oauth=oauth, enabled_tools=[]))
    return TestClient(app)


@pytest.mark.unit
class TestSseHeaderAuthNegatives:
    CLIENTS = [{"id": "assistant-agent", "scopes": ["aiops:search"]}]

    def test_messages_without_client_id_is_401(self, monkeypatch):
        assert _sse(monkeypatch, self.CLIENTS).post("/mcp/messages", content=b"{}").status_code == 401

    def test_messages_with_unknown_client_id_is_401(self, monkeypatch):
        resp = _sse(monkeypatch, self.CLIENTS).post(
            "/mcp/messages", content=b"{}", headers={"X-Client-Id": "intruder"})
        assert resp.status_code == 401

    def test_messages_with_allowed_client_id_passes_auth(self, monkeypatch):
        with pytest.raises(_PassedAuth):
            _sse(monkeypatch, self.CLIENTS).post(
                "/mcp/messages", content=b"{}", headers={"X-Client-Id": "assistant-agent"})

    def test_empty_allow_list_admits_nobody(self, monkeypatch):
        resp = _sse(monkeypatch, []).post(
            "/mcp/messages", content=b"{}", headers={"X-Client-Id": "assistant-agent"})
        assert resp.status_code == 401
