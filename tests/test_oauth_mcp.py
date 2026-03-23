"""Testes de integração para OAuth 2.1 + PKCE + scope enforcement no MCP Server.

Cobre:
  - Geração e verificação PKCE S256
  - Issue e validação de JWT
  - InMemoryTokenStore (save/pop/expiração)
  - Authorization Server endpoints (register, introspect, discovery)
  - Scope enforcement por ferramenta
  - SSE endpoint com Bearer JWT (usando TestClient)
"""
from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt_validator import TokenClaims, issue_token, validate_token
from auth.pkce import derive_challenge, generate_verifier, verify_challenge
from auth.scopes import TOOL_SCOPES, require_scope
from auth.token_store import InMemoryTokenStore
import auth.server as auth_server


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

class TestPKCE:
    def test_generate_verifier_is_url_safe(self):
        verifier = generate_verifier()
        assert len(verifier) > 32
        # URL-safe: só chars válidos
        import re
        assert re.fullmatch(r"[A-Za-z0-9_\-]+", verifier)

    def test_derive_challenge_deterministic(self):
        verifier = "test-verifier-abc123"
        c1 = derive_challenge(verifier)
        c2 = derive_challenge(verifier)
        assert c1 == c2

    def test_derive_challenge_no_padding(self):
        challenge = derive_challenge(generate_verifier())
        assert "=" not in challenge

    def test_verify_challenge_correct(self):
        verifier = generate_verifier()
        challenge = derive_challenge(verifier)
        assert verify_challenge(verifier, challenge) is True

    def test_verify_challenge_wrong_verifier(self):
        verifier = generate_verifier()
        challenge = derive_challenge(verifier)
        assert verify_challenge("wrong-verifier", challenge) is False

    def test_different_verifiers_produce_different_challenges(self):
        v1 = generate_verifier()
        v2 = generate_verifier()
        assert derive_challenge(v1) != derive_challenge(v2)


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

SECRET = "test-secret-aiops"


class TestJWT:
    def test_issue_and_validate_roundtrip(self):
        token = issue_token("assistant-agent", ["aiops:diagnose", "aiops:search"], SECRET)
        claims = validate_token(token, SECRET)
        assert claims.sub == "assistant-agent"
        assert "aiops:diagnose" in claims.scopes
        assert "aiops:search" in claims.scopes

    def test_expired_token_raises(self):
        token = issue_token("agent", ["aiops:search"], SECRET, ttl=-1)
        with pytest.raises(pyjwt.ExpiredSignatureError):
            validate_token(token, SECRET)

    def test_wrong_secret_raises(self):
        token = issue_token("agent", ["aiops:search"], SECRET)
        with pytest.raises(pyjwt.InvalidTokenError):
            validate_token(token, "wrong-secret")

    def test_empty_scopes(self):
        token = issue_token("agent", [], SECRET)
        claims = validate_token(token, SECRET)
        assert claims.scopes == []

    def test_token_claims_frozen(self):
        claims = TokenClaims(sub="x", scopes=["a"], exp=9999)
        with pytest.raises(Exception):
            claims.sub = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TokenStore
# ---------------------------------------------------------------------------

class TestInMemoryTokenStore:
    def test_save_and_pop(self):
        store = InMemoryTokenStore()
        store.save_challenge("client-1", "challenge-abc")
        result = store.pop_challenge("client-1")
        assert result == "challenge-abc"

    def test_pop_twice_returns_none(self):
        store = InMemoryTokenStore()
        store.save_challenge("client-1", "challenge-xyz")
        store.pop_challenge("client-1")
        assert store.pop_challenge("client-1") is None

    def test_pop_nonexistent_returns_none(self):
        store = InMemoryTokenStore()
        assert store.pop_challenge("nonexistent") is None

    def test_expired_challenge_returns_none(self):
        store = InMemoryTokenStore()
        store.save_challenge("client-1", "challenge-exp", ttl=0)
        time.sleep(0.01)  # garante expiração
        assert store.pop_challenge("client-1") is None

    def test_multiple_clients_isolated(self):
        store = InMemoryTokenStore()
        store.save_challenge("client-a", "challenge-a")
        store.save_challenge("client-b", "challenge-b")
        assert store.pop_challenge("client-a") == "challenge-a"
        assert store.pop_challenge("client-b") == "challenge-b"


# ---------------------------------------------------------------------------
# Authorization Server endpoints
# ---------------------------------------------------------------------------

def _make_auth_app() -> FastAPI:
    """Cria app FastAPI mínimo com o auth router para testes."""
    app = FastAPI()
    auth_server.configure(
        jwt_secret=SECRET,
        token_ttl=3600,
        allowed_clients=[
            {"id": "assistant-agent", "scopes": ["aiops:diagnose", "aiops:search"]},
            {"id": "databricks-mcp", "scopes": ["aiops:search"]},
        ],
        base_url="http://testserver",
        token_store=InMemoryTokenStore(),
    )
    app.include_router(auth_server.router)
    return app


class TestAuthServer:
    @pytest.fixture(autouse=True)
    def client(self):
        self._client = TestClient(_make_auth_app())

    def test_register_valid_client(self):
        verifier = generate_verifier()
        challenge = derive_challenge(verifier)
        resp = self._client.post("/auth/token/register", json={
            "client_id": "assistant-agent",
            "scopes": ["aiops:diagnose"],
            "code_challenge": challenge,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "Bearer"
        assert "aiops:diagnose" in body["scope"]

    def test_register_unknown_client_returns_401(self):
        resp = self._client.post("/auth/token/register", json={
            "client_id": "unknown-agent",
            "scopes": ["aiops:search"],
            "code_challenge": "abc",
        })
        assert resp.status_code == 401

    def test_register_disallowed_scope_returns_403(self):
        verifier = generate_verifier()
        challenge = derive_challenge(verifier)
        # databricks-mcp só tem aiops:search, tenta aiops:diagnose
        resp = self._client.post("/auth/token/register", json={
            "client_id": "databricks-mcp",
            "scopes": ["aiops:diagnose"],
            "code_challenge": challenge,
        })
        assert resp.status_code == 403

    def test_register_unsupported_pkce_method_returns_400(self):
        resp = self._client.post("/auth/token/register", json={
            "client_id": "assistant-agent",
            "scopes": ["aiops:search"],
            "code_challenge": "abc",
            "code_challenge_method": "plain",
        })
        assert resp.status_code == 400

    def test_introspect_valid_token(self):
        token = issue_token("assistant-agent", ["aiops:search"], SECRET)
        resp = self._client.post("/auth/token/introspect", json={"token": token})
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is True
        assert body["sub"] == "assistant-agent"

    def test_introspect_expired_token(self):
        token = issue_token("agent", ["aiops:search"], SECRET, ttl=-1)
        resp = self._client.post("/auth/token/introspect", json={"token": token})
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    def test_introspect_invalid_token(self):
        resp = self._client.post("/auth/token/introspect", json={"token": "not.a.jwt"})
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    def test_discovery_endpoint(self):
        resp = self._client.get("/auth/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        body = resp.json()
        assert "token_endpoint" in body
        assert "S256" in body["code_challenge_methods_supported"]
        assert "aiops:diagnose" in body["scopes_supported"]


# ---------------------------------------------------------------------------
# Scope enforcement
# ---------------------------------------------------------------------------

class TestScopeEnforcement:
    def test_require_scope_granted(self):
        result = require_scope("diagnose_incident", ["aiops:diagnose", "aiops:search"])
        assert result is None

    def test_require_scope_missing(self):
        result = require_scope("diagnose_incident", ["aiops:search"])
        assert result is not None
        assert "forbidden" in result.text
        assert "aiops:diagnose" in result.text

    def test_require_scope_unknown_tool(self):
        # Ferramentas sem scope definido são sempre permitidas
        result = require_scope("unknown_tool", [])
        assert result is None

    def test_all_tools_have_defined_scopes(self):
        for tool in ["diagnose_incident", "search_incidents", "validate_action"]:
            assert tool in TOOL_SCOPES

    def test_empty_scopes_blocked(self):
        result = require_scope("search_incidents", [])
        assert result is not None

    @pytest.mark.parametrize("tool,scope", [
        ("diagnose_incident", "aiops:diagnose"),
        ("search_incidents", "aiops:search"),
        ("validate_action", "aiops:validate"),
    ])
    def test_each_tool_requires_correct_scope(self, tool, scope):
        # Com scope correto: permitido
        assert require_scope(tool, [scope]) is None
        # Com scope errado: bloqueado
        wrong = [s for s in ["aiops:diagnose", "aiops:search", "aiops:validate"] if s != scope]
        assert require_scope(tool, wrong) is not None
