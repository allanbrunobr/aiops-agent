"""Correção pós-revisão #5: issuer (iss) validado nos resource servers.

Antes ``interfaces/mcp/sse.py`` e ``interfaces/http/routes/a2a.py`` chamavam
``validate_token(..., audience=...)`` SEM ``issuer=`` — o claim ``iss`` não era
checado (``auth/jwt_validator.py:83`` omite → PyJWT não valida). Um token assinado
com a chave correta mas emitido por OUTRO issuer passava. Só a introspecção
validava iss. Além disso o discovery anunciava ``issuer=base_url`` (="" no app
montado) enquanto os tokens tinham ``iss="aiops-agent"`` (mismatch RFC8414).

Fix: ``OAuthConfig.issuer`` (default "aiops-agent") é a fonte única — o AS emite
com ele (issue/introspect/discovery) e os resource servers (MCP, A2A, gestão HTTP)
o exigem. Token com iss errado → 401.
"""

from __future__ import annotations

import uuid

import jwt as pyjwt
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from auth.jwt_validator import issue_token
from auth.pkce import derive_challenge, generate_verifier
from auth.token_store import InMemoryTokenStore

SECRET = "issuer-validation-test-secret-32bytes!!"
_PING = '{"jsonrpc":"2.0","id":1,"method":"ping"}'


def _iss_of(token: str) -> str:
    return pyjwt.decode(token, options={"verify_signature": False})["iss"]


# ---------------------------------------------------------------------------
# A2A — _require_bearer exige iss
# ---------------------------------------------------------------------------


def _a2a_probe_app(issuer: str = "aiops-agent") -> FastAPI:
    from interfaces.http.routes.a2a import _require_bearer

    app = FastAPI()

    @app.get("/probe")
    def probe(claims=Depends(_require_bearer)):  # noqa: ANN001, ANN202
        return {"sub": claims.sub}

    app.state.a2a_jwt_secret = SECRET
    app.state.a2a_jwt_issuer = issuer
    return app


@pytest.mark.unit
def test_a2a_rejects_wrong_issuer():
    app = _a2a_probe_app()
    token = issue_token(
        "c", ["aiops:diagnose"], SECRET, audience="a2a", issuer="evil-issuer"
    )
    tc = TestClient(app, raise_server_exceptions=False)
    resp = tc.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401  # antes: 200 (iss não checado)


@pytest.mark.unit
def test_a2a_accepts_correct_issuer():
    app = _a2a_probe_app()
    token = issue_token(
        "c", ["aiops:diagnose"], SECRET, audience="a2a", issuer="aiops-agent"
    )
    tc = TestClient(app, raise_server_exceptions=False)
    resp = tc.get("/probe", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["sub"] == "c"


# ---------------------------------------------------------------------------
# MCP — oauth21 exige iss (POST autentica antes do lookup de sessão)
# ---------------------------------------------------------------------------


class _OAuth21:
    jwt_secret = SECRET
    issuer = "aiops-agent"
    allowed_clients: list = []
    trusted_issuers: list = []


class _McpCfg:
    auth_method = "oauth21"
    enabled_tools = ["search_incidents", "validate_action", "diagnose_incident"]
    transport = "sse"
    oauth = _OAuth21()


def _mcp_app() -> FastAPI:
    from interfaces.mcp.sse import mount_sse

    app = FastAPI()
    mount_sse(app, config=_McpCfg())
    return app


def _mcp_post(token: str):
    app = _mcp_app()
    tc = TestClient(app, raise_server_exceptions=False)
    return tc.post(
        f"/mcp/messages?session_id={uuid.uuid4().hex}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        content=_PING,
    )


@pytest.mark.unit
def test_mcp_rejects_wrong_issuer():
    token = issue_token(
        "c", ["aiops:search"], SECRET, audience="mcp", issuer="evil-issuer"
    )
    resp = _mcp_post(token)
    assert resp.status_code == 401  # antes: 404 (auth passava, iss não checado)


@pytest.mark.unit
def test_mcp_correct_issuer_passes_auth():
    token = issue_token(
        "c", ["aiops:search"], SECRET, audience="mcp", issuer="aiops-agent"
    )
    resp = _mcp_post(token)
    # auth OK → chega ao lookup de sessão (inexistente) → 404, não 401
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# AS — emite e anuncia o issuer configurado (consistência / RFC8414)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_auth_server_issues_and_advertises_configured_issuer():
    import auth.server as auth_server

    app = FastAPI()
    auth_server.configure(
        jwt_secret=SECRET,
        allowed_clients=[{"id": "assistant-agent", "scopes": ["aiops:diagnose"]}],
        base_url="http://testserver",
        issuer="https://as.example.com",
        token_store=InMemoryTokenStore(),
    )
    app.include_router(auth_server.router)
    tc = TestClient(app, raise_server_exceptions=False)

    verifier = generate_verifier()
    resp = tc.post(
        "/auth/authorize",
        json={
            "client_id": "assistant-agent",
            "scopes": ["aiops:diagnose"],
            "code_challenge": derive_challenge(verifier),
        },
    )
    assert resp.status_code == 200, resp.text
    code = resp.json()["code"]
    resp = tc.post(
        "/auth/token",
        json={
            "client_id": "assistant-agent",
            "code": code,
            "code_verifier": verifier,
        },
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    assert _iss_of(token) == "https://as.example.com"

    meta = tc.get("/auth/.well-known/oauth-authorization-server").json()
    # discovery anuncia o MESMO issuer dos tokens (antes: base_url, mismatch)
    assert meta["issuer"] == "https://as.example.com"
    # endpoints continuam derivados de base_url
    assert meta["token_endpoint"] == "http://testserver/auth/token"
