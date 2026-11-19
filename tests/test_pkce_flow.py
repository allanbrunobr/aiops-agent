"""Regressão F4-1: PKCE real com /authorize e /token separados.

Antes do fix: um único POST /auth/token/register recebia verifier E challenge
na MESMA request — a "prova" PKCE acontecia junto da emissão, sem exchange
separado; pop_challenge nunca era consumido em produção; verify_challenge
usava == (não constant-time).

Depois: /authorize recebe só o challenge (S256) e emite um code de uso único
vinculado a client+scopes+challenge; /token exige o verifier, valida contra
o challenge armazenado e CONSOME o code.
"""

from __future__ import annotations

import inspect
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth.server as auth_server
from auth.pkce import derive_challenge, generate_verifier, verify_challenge
from auth.token_store import InMemoryTokenStore

SECRET = "test-secret-key-with-at-least-32-bytes!!"


def _make_auth_app() -> FastAPI:
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


@pytest.fixture
def client():
    return TestClient(_make_auth_app())


def _authorize(client, client_id="assistant-agent", scopes=None, verifier=None):
    verifier = verifier or generate_verifier()
    resp = client.post(
        "/auth/authorize",
        json={
            "client_id": client_id,
            "scopes": scopes if scopes is not None else ["aiops:diagnose"],
            "code_challenge": derive_challenge(verifier),
        },
    )
    return resp, verifier


@pytest.mark.unit
class TestPkceTwoStepFlow:
    def test_full_flow_correct_verifier(self, client):
        resp, verifier = _authorize(client)
        assert resp.status_code == 200, resp.text
        code = resp.json()["code"]
        assert code

        resp = client.post(
            "/auth/token",
            json={
                "client_id": "assistant-agent",
                "code": code,
                "code_verifier": verifier,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["access_token"]
        assert body["token_type"] == "Bearer"
        assert "aiops:diagnose" in body["scope"]

    def test_wrong_verifier_fails(self, client):
        resp, _verifier = _authorize(client)
        code = resp.json()["code"]
        resp = client.post(
            "/auth/token",
            json={
                "client_id": "assistant-agent",
                "code": code,
                "code_verifier": generate_verifier(),
            },
        )
        assert resp.status_code == 400

    def test_missing_verifier_fails(self, client):
        resp, _verifier = _authorize(client)
        code = resp.json()["code"]
        resp = client.post(
            "/auth/token",
            json={"client_id": "assistant-agent", "code": code},
        )
        assert resp.status_code in (400, 422)

    def test_challenge_at_token_request_rejected(self, client):
        """Apresentar o challenge (em vez do verifier) no /token não emite token."""
        resp, verifier = _authorize(client)
        code = resp.json()["code"]
        resp = client.post(
            "/auth/token",
            json={
                "client_id": "assistant-agent",
                "code": code,
                "code_challenge": derive_challenge(verifier),
            },
        )
        assert resp.status_code in (400, 422)

    def test_code_replay_rejected(self, client):
        resp, verifier = _authorize(client)
        code = resp.json()["code"]
        payload = {
            "client_id": "assistant-agent",
            "code": code,
            "code_verifier": verifier,
        }
        assert client.post("/auth/token", json=payload).status_code == 200
        assert client.post("/auth/token", json=payload).status_code == 400

    def test_code_bound_to_client(self, client):
        resp, verifier = _authorize(client, client_id="assistant-agent")
        code = resp.json()["code"]
        resp = client.post(
            "/auth/token",
            json={
                "client_id": "databricks-mcp",
                "code": code,
                "code_verifier": verifier,
            },
        )
        assert resp.status_code == 400

    def test_unknown_code_rejected(self, client):
        resp = client.post(
            "/auth/token",
            json={
                "client_id": "assistant-agent",
                "code": "nonexistent-code",
                "code_verifier": generate_verifier(),
            },
        )
        assert resp.status_code == 400

    def test_unknown_client_authorize_401(self, client):
        resp, _ = _authorize(client, client_id="unknown-agent")
        assert resp.status_code == 401

    def test_disallowed_scope_authorize_403(self, client):
        resp, _ = _authorize(
            client, client_id="databricks-mcp", scopes=["aiops:diagnose"]
        )
        assert resp.status_code == 403

    def test_plain_method_rejected(self, client):
        resp = client.post(
            "/auth/authorize",
            json={
                "client_id": "assistant-agent",
                "scopes": ["aiops:search"],
                "code_challenge": "abc",
                "code_challenge_method": "plain",
            },
        )
        assert resp.status_code == 400

    def test_single_request_register_requires_client_auth(self, client):
        # SEC-01 (origin/main): o endpoint single-step /auth/token/register foi
        # reintroduzido, mas agora exige client_secret (fail-closed). Um pedido
        # sem segredo de cliente — o ataque do relatório — recebe 401, não um JWT.
        verifier = generate_verifier()
        resp = client.post(
            "/auth/token/register",
            json={
                "client_id": "assistant-agent",
                "scopes": ["aiops:search"],
                "code_verifier": verifier,
                "code_challenge": derive_challenge(verifier),
            },
        )
        assert resp.status_code == 401

    def test_discovery_advertises_two_step(self, client):
        resp = client.get("/auth/.well-known/oauth-authorization-server")
        assert resp.status_code == 200
        meta = resp.json()
        assert meta["authorization_endpoint"] == "http://testserver/auth/authorize"
        assert meta["token_endpoint"] == "http://testserver/auth/token"
        assert meta["code_challenge_methods_supported"] == ["S256"]


@pytest.mark.unit
class TestPkcePrimitives:
    def test_verify_challenge_constant_time(self):
        src = inspect.getsource(verify_challenge)
        assert "compare_digest" in src, "verify_challenge deve usar hmac.compare_digest"

    def test_verify_challenge_still_correct(self):
        verifier = generate_verifier()
        assert verify_challenge(verifier, derive_challenge(verifier)) is True
        assert (
            verify_challenge(generate_verifier(), derive_challenge(verifier)) is False
        )

    def test_store_code_ttl_and_one_shot(self):
        store = InMemoryTokenStore()
        store.save_code("code-1", "client-a", ["s"], "challenge-a", ttl=300)
        entry = store.pop_code("code-1")
        assert entry is not None
        assert entry["client_id"] == "client-a"
        assert entry["challenge"] == "challenge-a"
        assert store.pop_code("code-1") is None  # uso único

        store.save_code("code-2", "client-a", ["s"], "challenge-b", ttl=0)
        time.sleep(0.01)  # garante que o relógio avançou além da expiração
        assert store.pop_code("code-2") is None  # expirado
