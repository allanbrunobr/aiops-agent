"""Regressão F4-5: POST /mcp/messages só aceita o principal que abriu a sessão.

Antes do fix: o POST autenticava qualquer cliente válido, mas não verificava
se o `session_id` pertencia a ele. Um session_id vazado (ele viaja na URL do
evento `endpoint`) permitia que OUTRO cliente autenticado injetasse mensagens
JSON-RPC na sessão de alguém. Agora GET e POST registram o principal em
`scope["user"]`; o SseServerTransport do SDK compara quem abriu com quem
envia e responde 404 (indistinguível de sessão inexistente) em mismatch.

Os testes não abrem um stream SSE real (longa duração, frágil em TestClient):
simulam a sessão aberta registrando no transporte exatamente o principal que
`interfaces.mcp.sse._principal_user` constrói para o GET — o mesmo caminho de
código que o endpoint usa — e exercitam o POST de verdade.
"""

from __future__ import annotations

import uuid

import anyio
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.auth.middleware.bearer_auth import authorization_context

import interfaces.mcp.sse as sse_mod
from interfaces.mcp.sse import _principal_user, mount_sse

_PING = '{"jsonrpc":"2.0","id":1,"method":"ping"}'


class _FakeOAuth:
    jwt_secret = ""
    allowed_clients = [
        {"id": "client-a", "scopes": ["aiops:search"], "secret": "s3cret-a"},
        {"id": "client-b", "scopes": ["aiops:search"], "secret": "s3cret-b"},
    ]
    trusted_issuers = []


class _FakeConfig:
    auth_method = "header"
    enabled_tools = ["search_incidents", "validate_action", "diagnose_incident"]
    transport = "sse"
    oauth = _FakeOAuth()


def _headers(client: str) -> dict[str, str]:
    return {"X-Client-Id": client, "X-Client-Secret": f"s3cret-{client[-1]}"}


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    mount_sse(app, config=_FakeConfig())
    return app


@pytest.fixture
def session_of_client_a():
    """Sessão SSE "aberta" por client-a, sem stream longo.

    Registra no transporte o que `connect_sse` registraria: um writer para o
    session_id e o owner derivado do `scope["user"]` que o GET define.
    Buffer > 0 para que o POST aceito não bloqueie à espera de um leitor.
    """
    transport = sse_mod._sse_transport
    assert transport is not None
    session_id = uuid.uuid4()
    send, recv = anyio.create_memory_object_stream(max_buffer_size=8)
    transport._read_stream_writers[session_id] = send
    transport._session_owners[session_id] = authorization_context(
        _principal_user("client-a", ["aiops:search"])
    )
    try:
        yield session_id.hex
    finally:
        transport._read_stream_writers.pop(session_id, None)
        transport._session_owners.pop(session_id, None)
        send.close()
        recv.close()


def _post(app: FastAPI, session_hex: str, client: str):
    tc = TestClient(app, raise_server_exceptions=False)
    return tc.post(
        f"/mcp/messages?session_id={session_hex}",
        headers={**_headers(client), "Content-Type": "application/json"},
        content=_PING,
    )


@pytest.mark.unit
def test_same_client_can_post_to_its_session(app, session_of_client_a):
    resp = _post(app, session_of_client_a, "client-a")
    assert resp.status_code == 202, resp.text


@pytest.mark.unit
def test_other_authenticated_client_cannot_post_to_foreign_session(
    app, session_of_client_a
):
    """client-b autentica (segredo correto) mas a sessão é de client-a → 404."""
    resp = _post(app, session_of_client_a, "client-b")
    assert resp.status_code == 404
    # Mesma resposta de sessão inexistente: não vaza que o session_id é válido
    assert resp.text == "Could not find session"


@pytest.mark.unit
def test_unauthenticated_post_is_401_before_session_lookup(app, session_of_client_a):
    tc = TestClient(app, raise_server_exceptions=False)
    resp = tc.post(
        f"/mcp/messages?session_id={session_of_client_a}",
        headers={"X-Client-Id": "client-b"},  # sem secret
        content=_PING,
    )
    assert resp.status_code == 401


@pytest.mark.unit
def test_principal_is_bound_by_client_id():
    """Garante que o binding usa a identidade autenticada, não algo constante."""
    a = authorization_context(_principal_user("client-a", ["aiops:search"]))
    b = authorization_context(_principal_user("client-b", ["aiops:search"]))
    assert a != b
    assert a == authorization_context(_principal_user("client-a", ["x"]))
