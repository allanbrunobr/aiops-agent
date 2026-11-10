"""/auth/token/register exige autenticação do cliente.

Regressão de SEC-01 (docs/analise-20261109.md): o endpoint emitia JWT para
quem soubesse um client_id (publicados em config/defaults.yaml). O PKCE era
provado pelo próprio requisitante na mesma requisição — verifier e challenge
no mesmo corpo — e não havia segredo de cliente.

Agora cada cliente precisa de `secret_sha256` configurado e de enviar
`client_secret`. Cliente sem hash configurado não recebe token (fail-closed).
Os segredos destes testes são gerados em tempo de execução.
"""
from __future__ import annotations

import hashlib
import secrets

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth.server as auth_server
from auth.keys import generate_rsa_keypair
from auth.pkce import derive_challenge, generate_verifier
from auth.token_store import InMemoryTokenStore

HMAC_KEY = secrets.token_urlsafe(48)
CLIENT_SECRET = secrets.token_urlsafe(32)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _app(**configure_overrides) -> TestClient:
    app = FastAPI()
    kwargs = dict(
        jwt_secret=HMAC_KEY,
        allowed_clients=[
            {"id": "sre-orchestrator",
             "scopes": ["aiops:diagnose", "aiops:search", "aiops:validate"],
             "secret_sha256": _sha256(CLIENT_SECRET)},
            # Publicado no YAML, mas sem segredo configurado.
            {"id": "assistant-agent", "scopes": ["aiops:diagnose", "aiops:search"]},
        ],
        base_url="http://testserver",
        token_store=InMemoryTokenStore(),
    )
    kwargs.update(configure_overrides)
    auth_server.configure(**kwargs)
    app.include_router(auth_server.router)
    return TestClient(app)


def _body(client_id: str, secret: str | None) -> dict:
    verifier = generate_verifier()
    body = {
        "client_id": client_id,
        "scopes": [],
        "code_verifier": verifier,
        "code_challenge": derive_challenge(verifier),
    }
    if secret is not None:
        body["client_secret"] = secret
    return body


@pytest.mark.unit
class TestClientAuthentication:
    def test_self_proved_pkce_without_secret_is_rejected(self):
        """O ataque do relatório: só client_id público + PKCE gerado na hora."""
        resp = _app().post("/auth/token/register", json=_body("sre-orchestrator", None))
        assert resp.status_code == 401
        assert "access_token" not in resp.json()

    def test_wrong_secret_is_rejected(self):
        resp = _app().post("/auth/token/register",
                           json=_body("sre-orchestrator", secrets.token_urlsafe(32)))
        assert resp.status_code == 401

    def test_client_without_configured_secret_is_rejected(self):
        """Fail-closed: id publicado no YAML sem hash configurado não recebe token."""
        resp = _app().post("/auth/token/register",
                           json=_body("assistant-agent", CLIENT_SECRET))
        assert resp.status_code == 401

    def test_correct_secret_and_pkce_issue_token(self):
        resp = _app().post("/auth/token/register",
                           json=_body("sre-orchestrator", CLIENT_SECRET))
        assert resp.status_code == 200
        assert resp.json()["access_token"]

    def test_correct_secret_still_needs_valid_pkce(self):
        body = _body("sre-orchestrator", CLIENT_SECRET)
        body["code_verifier"] = generate_verifier()  # não corresponde ao challenge
        resp = _app().post("/auth/token/register", json=body)
        assert resp.status_code == 400


@pytest.mark.unit
class TestDiscoveryAndSigningMode:
    def test_discovery_advertises_client_secret_post(self):
        meta = _app().get("/auth/.well-known/oauth-authorization-server").json()
        assert meta["token_endpoint_auth_methods_supported"] == ["client_secret_post"]

    def test_rsa_only_mode_returns_503_not_500(self):
        """issue_token só assina HS256: sem chave HMAC, recusa explícita."""
        private_pem, public_pem = generate_rsa_keypair()
        client = _app(jwt_secret="", rsa_private_pem=private_pem, rsa_public_pem=public_pem)
        resp = client.post("/auth/token/register",
                           json=_body("sre-orchestrator", CLIENT_SECRET))
        assert resp.status_code == 503


@pytest.mark.unit
class TestConfigWiring:
    def test_create_app_passes_secret_hash_to_auth_server(self):
        from config.loader import AppConfig
        from interfaces.http.app import create_app

        config = AppConfig(mcp={"enabled": False, "oauth": {
            "jwt_secret": HMAC_KEY,
            "allowed_clients": [{
                "id": "sre-orchestrator",
                "scopes": ["aiops:search"],
                "secret_sha256": _sha256(CLIENT_SECRET),
            }],
        }})
        with TestClient(create_app(config=config)) as client:
            ok = client.post("/auth/token/register",
                             json=_body("sre-orchestrator", CLIENT_SECRET))
            bad = client.post("/auth/token/register",
                              json=_body("sre-orchestrator", None))
        assert ok.status_code == 200
        assert bad.status_code == 401
