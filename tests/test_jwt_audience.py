"""Regressão F4-3: JWT com iss/aud por audiência + RS256 real (sem 500).

Antes do fix: tokens sem iss/aud e mesmo segredo MCP/A2A → token MCP valia
no A2A; RS256 anunciado no metadata/JWKS mas issue_token sempre HS256;
RSA configurado + secret curto → InvalidKeyError não tratado → 500.
"""

from __future__ import annotations

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth.server as auth_server
from auth.jwt_validator import validate_token
from auth.keys import generate_rsa_keypair
from auth.pkce import derive_challenge, generate_verifier
from auth.token_store import InMemoryTokenStore

SECRET = "test-secret-key-with-at-least-32-bytes!!"


def _make_auth_app(rsa: tuple[str, str] | None = None, secret: str = SECRET) -> FastAPI:
    app = FastAPI()
    kwargs: dict = dict(
        jwt_secret=secret,
        token_ttl=3600,
        allowed_clients=[
            {"id": "assistant-agent", "scopes": ["aiops:diagnose", "aiops:search"]}
        ],
        base_url="http://testserver",
        token_store=InMemoryTokenStore(),
    )
    if rsa:
        kwargs.update(rsa_private_pem=rsa[0], rsa_public_pem=rsa[1])
    auth_server.configure(**kwargs)
    app.include_router(auth_server.router)
    return app


def _get_token(client: TestClient, audience: str | None = None) -> str:
    verifier = generate_verifier()
    body: dict = {
        "client_id": "assistant-agent",
        "scopes": ["aiops:search"],
        "code_challenge": derive_challenge(verifier),
    }
    if audience is not None:
        body["audience"] = audience
    resp = client.post("/auth/authorize", json=body)
    assert resp.status_code == 200, resp.text
    resp = client.post(
        "/auth/token",
        json={
            "client_id": "assistant-agent",
            "code": resp.json()["code"],
            "code_verifier": verifier,
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


@pytest.mark.unit
def test_token_carries_iss_and_aud():
    client = TestClient(_make_auth_app())
    token = _get_token(client)
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["iss"] == "http://testserver"
    assert claims["aud"] == "mcp"


@pytest.mark.unit
def test_audience_a2a_bound_at_authorize():
    client = TestClient(_make_auth_app())
    token = _get_token(client, audience="a2a")
    claims = pyjwt.decode(token, options={"verify_signature": False})
    assert claims["aud"] == "a2a"


@pytest.mark.unit
def test_invalid_audience_rejected():
    client = TestClient(_make_auth_app())
    verifier = generate_verifier()
    resp = client.post(
        "/auth/authorize",
        json={
            "client_id": "assistant-agent",
            "scopes": ["aiops:search"],
            "code_challenge": derive_challenge(verifier),
            "audience": "evil-api",
        },
    )
    assert resp.status_code == 400


@pytest.mark.unit
def test_cross_audience_validation_rejected():
    client = TestClient(_make_auth_app())
    mcp_token = _get_token(client)
    a2a_token = _get_token(client, audience="a2a")

    # Token MCP não vale no A2A e vice-versa
    with pytest.raises(pyjwt.InvalidAudienceError):
        validate_token(mcp_token, SECRET, audience="a2a")
    with pytest.raises(pyjwt.InvalidAudienceError):
        validate_token(a2a_token, SECRET, audience="mcp")

    assert validate_token(mcp_token, SECRET, audience="mcp").sub == "assistant-agent"
    assert validate_token(a2a_token, SECRET, audience="a2a").sub == "assistant-agent"


@pytest.mark.unit
def test_a2a_endpoint_rejects_mcp_token():
    from interfaces.http.routes.a2a import router as a2a_router

    app = FastAPI()
    app.state.a2a_jwt_secret = SECRET
    app.state.a2a_jwt_public_pem = ""
    app.include_router(a2a_router)
    client = TestClient(app, raise_server_exceptions=False)

    mcp_app = TestClient(_make_auth_app())
    mcp_token = _get_token(mcp_app)

    resp = client.post(
        "/a2a/tasks",
        json={"jsonrpc": "2.0", "method": "tasks/send", "id": "1", "params": {}},
        headers={"Authorization": f"Bearer {mcp_token}"},
    )
    assert resp.status_code == 401


@pytest.mark.unit
def test_rs256_real_with_short_secret_no_500():
    rsa = generate_rsa_keypair()
    client = TestClient(_make_auth_app(rsa=rsa, secret="short"))
    token = _get_token(client)

    header = pyjwt.get_unverified_header(token)
    assert header["alg"] == "RS256"
    claims = validate_token(token, rsa[1], algorithm="RS256", audience="mcp")
    assert claims.sub == "assistant-agent"

    jwks = client.get("/auth/.well-known/jwks.json").json()
    assert jwks["keys"], "JWKS deve publicar a chave RSA realmente usada"


@pytest.mark.unit
def test_introspect_accepts_both_audiences():
    client = TestClient(_make_auth_app())
    for aud in (None, "a2a"):
        token = _get_token(client, audience=aud)
        resp = client.post("/auth/token/introspect", json={"token": token})
        assert resp.status_code == 200
        assert resp.json()["active"] is True
