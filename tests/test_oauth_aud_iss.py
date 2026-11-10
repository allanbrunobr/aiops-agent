"""`aud`/`iss` configuráveis nos tokens (SEC-11). Desligados por padrão.

Quando `mcp.oauth.issuer` e/ou `mcp.oauth.audience` estão configurados, o
/auth/token/register emite os claims e todos os validadores (introspect, A2A,
SSE) passam a exigi-los. Vazio = comportamento anterior. Qual URL usar é
decisão pendente do Allan (docs/p1-progresso.md).
"""
from __future__ import annotations

import hashlib
import secrets

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from auth.jwt_validator import issue_token, validate_token
from auth.pkce import derive_challenge, generate_verifier

KEY = secrets.token_urlsafe(48)
CLIENT_SECRET = secrets.token_urlsafe(32)
ISS = "https://aiops.example.internal"
AUD = "aiops-agent"


@pytest.mark.unit
class TestValidator:
    def test_round_trip_with_iss_and_aud(self):
        token = issue_token("a", ["aiops:search"], KEY, issuer=ISS, audience=AUD)
        assert validate_token(token, KEY, issuer=ISS, audience=AUD).sub == "a"

    def test_wrong_audience_is_rejected(self):
        token = issue_token("a", [], KEY, issuer=ISS, audience="other")
        with pytest.raises(pyjwt.InvalidTokenError):
            validate_token(token, KEY, issuer=ISS, audience=AUD)

    def test_token_without_claims_is_rejected_when_required(self):
        token = issue_token("a", [], KEY)
        with pytest.raises(pyjwt.InvalidTokenError):
            validate_token(token, KEY, issuer=ISS, audience=AUD)

    def test_default_has_no_claims_and_validates(self):
        token = issue_token("a", [], KEY)
        payload = pyjwt.decode(token, options={"verify_signature": False})
        assert "aud" not in payload and "iss" not in payload
        assert validate_token(token, KEY).sub == "a"


def _app(issuer: str, audience: str):
    from config.loader import AppConfig
    from interfaces.http.app import create_app

    config = AppConfig(mcp={"enabled": False, "oauth": {
        "jwt_secret": KEY, "issuer": issuer, "audience": audience,
        "allowed_clients": [{
            "id": "sre-orchestrator", "scopes": ["aiops:diagnose", "aiops:search"],
            "secret_sha256": hashlib.sha256(CLIENT_SECRET.encode()).hexdigest(),
        }],
    }})
    return create_app(config=config)


def _register(client) -> str:
    v = generate_verifier()
    resp = client.post("/auth/token/register", json={
        "client_id": "sre-orchestrator", "client_secret": CLIENT_SECRET, "scopes": [],
        "code_verifier": v, "code_challenge": derive_challenge(v),
    })
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _a2a_status(client, token: str) -> int:
    resp = client.post("/a2a/tasks", json={
        "jsonrpc": "2.0", "id": "1", "method": "tasks/send",
        "params": {"id": "t", "skill": {"id": "search_incidents"},
                   "message": {"role": "user", "parts": [{"type": "data", "data": {"query": "x"}}]}},
    }, headers={"Authorization": f"Bearer {token}"})
    return resp.status_code


@pytest.mark.unit
class TestConfiguredServer:
    def test_register_emits_configured_claims(self):
        with TestClient(_app(ISS, AUD)) as client:
            token = _register(client)
        payload = pyjwt.decode(token, options={"verify_signature": False})
        assert payload["iss"] == ISS
        assert payload["aud"] == AUD

    def test_introspect_and_a2a_accept_matching_token(self):
        with TestClient(_app(ISS, AUD)) as client:
            token = _register(client)
            assert client.post("/auth/token/introspect", json={"token": token}).json()["active"]
            assert _a2a_status(client, token) != 401

    def test_a2a_rejects_token_without_audience(self):
        with TestClient(_app(ISS, AUD)) as client:
            plain = issue_token("sre-orchestrator", ["aiops:search"], KEY)
            assert _a2a_status(client, plain) == 401
            assert client.post("/auth/token/introspect",
                               json={"token": plain}).json()["active"] is False

    def test_unconfigured_server_keeps_previous_behaviour(self):
        with TestClient(_app("", "")) as client:
            token = _register(client)
            payload = pyjwt.decode(token, options={"verify_signature": False})
            assert "aud" not in payload
            assert _a2a_status(client, issue_token("x", ["aiops:search"], KEY)) != 401
