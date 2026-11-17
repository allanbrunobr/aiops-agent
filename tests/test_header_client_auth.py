"""Regressão F4-2: modo header autentica cliente por segredo, não só por id.

Antes do fix: bastava enviar X-Client-Id com um id publicado no YAML para
autenticar — nenhum segredo. Agora cada cliente precisa de um secret
configurado (OAuthClientConfig.secret) e o header X-Client-Secret é
obrigatório, comparado em tempo constante. Cliente sem secret configurado
não autentica (fail-closed).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from interfaces.mcp.sse import mount_sse


class _FakeOAuth:
    jwt_secret = ""
    allowed_clients = [
        {"id": "client-a", "scopes": ["aiops:search"], "secret": "s3cret-a"},
        {"id": "client-nosecret", "scopes": ["aiops:search"], "secret": ""},
    ]
    trusted_issuers = []


class _FakeConfig:
    auth_method = "header"
    enabled_tools = ["search_incidents", "validate_action", "diagnose_incident"]
    transport = "sse"
    oauth = _FakeOAuth()


def _make_app() -> FastAPI:
    app = FastAPI()
    mount_sse(app, config=_FakeConfig())
    return app


def _post(app, headers: dict):
    client = TestClient(app, raise_server_exceptions=False)
    return client.post("/mcp/messages?session_id=unknown-session", headers=headers)


@pytest.mark.unit
def test_header_without_secret_rejected():
    resp = _post(_make_app(), {"X-Client-Id": "client-a"})
    assert resp.status_code == 401


@pytest.mark.unit
def test_header_with_wrong_secret_rejected():
    resp = _post(_make_app(), {"X-Client-Id": "client-a", "X-Client-Secret": "wrong"})
    assert resp.status_code == 401


@pytest.mark.unit
def test_header_with_correct_secret_passes_auth():
    resp = _post(
        _make_app(), {"X-Client-Id": "client-a", "X-Client-Secret": "s3cret-a"}
    )
    # Auth passou; a sessão desconhecida é erro do transporte (404), não 401
    assert resp.status_code != 401


@pytest.mark.unit
def test_client_without_configured_secret_fail_closed():
    resp = _post(
        _make_app(),
        {"X-Client-Id": "client-nosecret", "X-Client-Secret": "anything"},
    )
    assert resp.status_code == 401


@pytest.mark.unit
def test_unknown_client_id_still_rejected():
    resp = _post(
        _make_app(), {"X-Client-Id": "intruder", "X-Client-Secret": "s3cret-a"}
    )
    assert resp.status_code == 401
