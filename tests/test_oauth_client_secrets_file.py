"""Hashes dos client secrets OAuth lidos de um arquivo fora do repositório.

O `defaults.yaml` é versionado e não deve carregar `secret_sha256`. O
caminho do arquivo vem de `mcp.oauth.client_secrets_file`, normalmente pela
variável AIOPS_MCP__OAUTH__CLIENT_SECRETS_FILE. Formato: JSON
{client_id: sha256 hex}. Arquivo ausente ou inválido => nenhum hash
(fail-closed: nenhum cliente recebe token).
Os segredos destes testes são gerados em tempo de execução.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets

import pytest
from fastapi.testclient import TestClient

from auth.client_secrets import load_client_secret_hashes
from auth.pkce import derive_challenge, generate_verifier

HMAC_KEY = secrets.token_urlsafe(48)
CLIENT_SECRET = secrets.token_urlsafe(32)
CLIENT_HASH = hashlib.sha256(CLIENT_SECRET.encode()).hexdigest()


def _write(tmp_path, data, mode=0o600):
    path = tmp_path / "oauth-clients.sha256.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data))
    os.chmod(path, mode)
    return path


@pytest.mark.unit
class TestLoadClientSecretHashes:
    def test_valid_file(self, tmp_path):
        path = _write(tmp_path, {"sre-orchestrator": CLIENT_HASH})
        assert load_client_secret_hashes(str(path)) == {"sre-orchestrator": CLIENT_HASH}

    def test_no_path_means_no_hashes(self):
        assert load_client_secret_hashes("") == {}

    def test_missing_file_fails_closed(self, tmp_path):
        assert load_client_secret_hashes(str(tmp_path / "nope.json")) == {}

    @pytest.mark.parametrize("content", ["not json", "[1, 2]", '{"a": "zz"}', '{"a": 1}'])
    def test_invalid_content_fails_closed(self, tmp_path, content):
        assert load_client_secret_hashes(str(_write(tmp_path, content))) == {}

    def test_invalid_entries_are_dropped(self, tmp_path):
        path = _write(tmp_path, {"good": CLIENT_HASH, "bad": "123"})
        assert load_client_secret_hashes(str(path)) == {"good": CLIENT_HASH}

    def test_group_or_world_readable_file_is_rejected(self, tmp_path):
        path = _write(tmp_path, {"sre-orchestrator": CLIENT_HASH}, mode=0o644)
        assert load_client_secret_hashes(str(path)) == {}


@pytest.mark.unit
class TestConfigAndApp:
    def test_env_variable_sets_the_path(self, monkeypatch, tmp_path):
        from config.loader import AppConfig

        path = str(tmp_path / "x.json")
        monkeypatch.setenv("AIOPS_MCP__OAUTH__CLIENT_SECRETS_FILE", path)
        assert AppConfig().mcp.oauth.client_secrets_file == path

    def _register(self, client, secret):
        verifier = generate_verifier()
        return client.post("/auth/token/register", json={
            "client_id": "sre-orchestrator", "client_secret": secret, "scopes": [],
            "code_verifier": verifier, "code_challenge": derive_challenge(verifier),
        })

    def test_create_app_uses_hashes_from_file(self, tmp_path):
        from config.loader import AppConfig
        from interfaces.http.app import create_app

        path = _write(tmp_path, {"sre-orchestrator": CLIENT_HASH})
        config = AppConfig(mcp={"enabled": False, "oauth": {
            "jwt_secret": HMAC_KEY,
            "client_secrets_file": str(path),
            "allowed_clients": [{"id": "sre-orchestrator", "scopes": ["aiops:search"]}],
        }})
        with TestClient(create_app(config=config)) as client:
            assert self._register(client, CLIENT_SECRET).status_code == 200
            assert self._register(client, secrets.token_urlsafe(32)).status_code == 401

    def test_create_app_without_file_issues_nothing(self):
        from config.loader import AppConfig
        from interfaces.http.app import create_app

        config = AppConfig(mcp={"enabled": False, "oauth": {
            "jwt_secret": HMAC_KEY,
            "allowed_clients": [{"id": "sre-orchestrator", "scopes": ["aiops:search"]}],
        }})
        with TestClient(create_app(config=config)) as client:
            assert self._register(client, CLIENT_SECRET).status_code == 401
