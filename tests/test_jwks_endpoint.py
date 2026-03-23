"""Testes para Sprint 1 — auth/keys.py e endpoint JWKS no auth/server.py.

Cobertura:
  - generate_rsa_keypair: formato PEM, par consistente
  - public_key_to_jwk: campos obrigatórios RFC 7517, tipo RSA
  - load_or_generate_keypair: gera se não existe, carrega se existe
  - GET /.well-known/jwks.json: retorna keys=[] sem RS256, retorna JWK com RS256
  - GET /.well-known/oauth-authorization-server: jwks_uri ausente/presente
  - issue_token + validate_token com RS256 via PyJWT
"""
from __future__ import annotations

import base64
import json
import tempfile
import time
from pathlib import Path

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

from auth.keys import generate_rsa_keypair, load_or_generate_keypair, public_key_to_jwk


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[str, str]:
    """Par RSA gerado uma vez por módulo de teste (geração é lenta)."""
    return generate_rsa_keypair(key_size=2048)


@pytest.fixture(scope="module")
def private_pem(rsa_keypair: tuple[str, str]) -> str:
    return rsa_keypair[0]


@pytest.fixture(scope="module")
def public_pem(rsa_keypair: tuple[str, str]) -> str:
    return rsa_keypair[1]


@pytest.fixture(scope="module")
def jwk_dict(public_pem: str) -> dict:
    return public_key_to_jwk(public_pem, kid="test-key-1")


# ---------------------------------------------------------------------------
# Testes: auth/keys.py
# ---------------------------------------------------------------------------

class TestGenerateRsaKeypair:
    """Testes para generate_rsa_keypair()."""

    @pytest.mark.unit
    def test_returns_pem_strings(self, private_pem: str, public_pem: str) -> None:
        assert private_pem.startswith("-----BEGIN PRIVATE KEY-----")
        assert public_pem.startswith("-----BEGIN PUBLIC KEY-----")

    @pytest.mark.unit
    def test_private_pem_ends_correctly(self, private_pem: str) -> None:
        assert "-----END PRIVATE KEY-----" in private_pem

    @pytest.mark.unit
    def test_public_pem_ends_correctly(self, public_pem: str) -> None:
        assert "-----END PUBLIC KEY-----" in public_pem

    @pytest.mark.unit
    def test_pair_is_consistent(self, private_pem: str, public_pem: str) -> None:
        """Verifica que a chave privada e pública formam um par consistente."""
        from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
        priv = load_pem_private_key(private_pem.encode(), password=None)
        pub = load_pem_public_key(public_pem.encode())
        assert priv.public_key().public_numbers() == pub.public_numbers()  # type: ignore[attr-defined]

    @pytest.mark.unit
    def test_keys_are_different_each_call(self) -> None:
        priv1, pub1 = generate_rsa_keypair(key_size=2048)
        priv2, pub2 = generate_rsa_keypair(key_size=2048)
        assert priv1 != priv2
        assert pub1 != pub2


class TestPublicKeyToJwk:
    """Testes para public_key_to_jwk()."""

    @pytest.mark.unit
    def test_required_rfc7517_fields(self, jwk_dict: dict) -> None:
        for field in ("kty", "use", "alg", "kid", "n", "e"):
            assert field in jwk_dict, f"Campo '{field}' ausente no JWK"

    @pytest.mark.unit
    def test_kty_is_rsa(self, jwk_dict: dict) -> None:
        assert jwk_dict["kty"] == "RSA"

    @pytest.mark.unit
    def test_alg_is_rs256(self, jwk_dict: dict) -> None:
        assert jwk_dict["alg"] == "RS256"

    @pytest.mark.unit
    def test_use_is_sig(self, jwk_dict: dict) -> None:
        assert jwk_dict["use"] == "sig"

    @pytest.mark.unit
    def test_kid_matches(self, jwk_dict: dict) -> None:
        assert jwk_dict["kid"] == "test-key-1"

    @pytest.mark.unit
    def test_n_is_base64url(self, jwk_dict: dict) -> None:
        """n deve ser base64url válido sem padding."""
        n = jwk_dict["n"]
        assert "=" not in n
        # base64url decode deve funcionar sem erro
        padded = n + "=" * (4 - len(n) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        assert len(decoded) >= 256  # 2048-bit key → modulus ≥ 256 bytes

    @pytest.mark.unit
    def test_e_is_65537(self, jwk_dict: dict) -> None:
        """e deve ser 65537 (AQAB em base64url)."""
        e_bytes = base64.urlsafe_b64decode(jwk_dict["e"] + "==")
        e_int = int.from_bytes(e_bytes, "big")
        assert e_int == 65537

    @pytest.mark.unit
    def test_jwk_roundtrip_with_pyjwt(self, jwk_dict: dict, private_pem: str) -> None:
        """Token RS256 emitido com private PEM deve ser validado via JWK."""
        from jwt.algorithms import RSAAlgorithm

        token = pyjwt.encode(
            {"sub": "test-agent", "exp": int(time.time()) + 60},
            private_pem,
            algorithm="RS256",
        )
        public_key = RSAAlgorithm.from_jwk(jwk_dict)
        payload = pyjwt.decode(token, public_key, algorithms=["RS256"])
        assert payload["sub"] == "test-agent"


class TestLoadOrGenerateKeypair:
    """Testes para load_or_generate_keypair()."""

    @pytest.mark.unit
    def test_generates_if_not_exists(self, tmp_path: Path) -> None:
        priv_path = tmp_path / "private.pem"
        pub_path = tmp_path / "public.pem"
        priv, pub, jwk = load_or_generate_keypair(priv_path, pub_path, kid="gen-1")
        assert priv_path.exists()
        assert pub_path.exists()
        assert jwk["kid"] == "gen-1"
        assert jwk["kty"] == "RSA"

    @pytest.mark.unit
    def test_loads_existing_files(self, tmp_path: Path) -> None:
        priv_path = tmp_path / "private.pem"
        pub_path = tmp_path / "public.pem"
        # Primeira chamada: gera
        priv1, pub1, _ = load_or_generate_keypair(priv_path, pub_path)
        # Segunda chamada: carrega (sem regenerar)
        priv2, pub2, _ = load_or_generate_keypair(priv_path, pub_path)
        assert priv1 == priv2
        assert pub1 == pub2


# ---------------------------------------------------------------------------
# Testes: JWKS endpoint e discovery
# ---------------------------------------------------------------------------

class TestJwksEndpoint:
    """Testes para GET /.well-known/jwks.json no auth/server.py."""

    @pytest.mark.unit
    def test_returns_empty_keys_without_rsa(self) -> None:
        from server.app import create_app
        from config.loader import AppConfig

        config = AppConfig(
            mcp={"enabled": False, "oauth": {"jwt_secret": "test-secret-abc123"}},
        )
        app = create_app(config=config)
        with TestClient(app) as client:
            resp = client.get("/auth/.well-known/jwks.json")
            assert resp.status_code == 200
            body = resp.json()
            assert body == {"keys": []}

    @pytest.mark.unit
    def test_returns_jwk_with_rsa_configured(
        self, private_pem: str, public_pem: str, tmp_path: Path
    ) -> None:
        """create_app carrega chaves RSA dos paths configurados."""
        priv_path = tmp_path / "private.pem"
        pub_path = tmp_path / "public.pem"
        priv_path.write_text(private_pem)
        pub_path.write_text(public_pem)

        from server.app import create_app
        from config.loader import AppConfig

        config = AppConfig(
            mcp={
                "enabled": False,
                "oauth": {
                    "jwt_secret": "test-secret-for-jwks",
                    "private_key_path": str(priv_path),
                    "public_key_path": str(pub_path),
                    "kid": "endpoint-test-key",
                },
            },
        )
        app = create_app(config=config)
        with TestClient(app) as client:
            resp = client.get("/auth/.well-known/jwks.json")
            assert resp.status_code == 200
            body = resp.json()
            assert len(body["keys"]) == 1
            key = body["keys"][0]
            assert key["kty"] == "RSA"
            assert key["alg"] == "RS256"
            assert key["kid"] == "endpoint-test-key"
            assert "n" in key
            assert "e" in key


class TestDiscoveryWithJwksUri:
    """Testes para o campo jwks_uri no discovery endpoint."""

    @pytest.mark.unit
    def test_discovery_without_rsa_has_no_jwks_uri(self) -> None:
        from server.app import create_app
        from config.loader import AppConfig

        config = AppConfig(
            mcp={"enabled": False, "oauth": {"jwt_secret": "no-rsa-secret"}},
        )
        app = create_app(config=config)
        with TestClient(app) as client:
            resp = client.get("/auth/.well-known/oauth-authorization-server")
            assert resp.status_code == 200
            body = resp.json()
            assert "jwks_uri" not in body

    @pytest.mark.unit
    def test_discovery_with_rsa_has_jwks_uri(
        self, private_pem: str, public_pem: str, tmp_path: Path
    ) -> None:
        priv_path = tmp_path / "priv2.pem"
        pub_path = tmp_path / "pub2.pem"
        priv_path.write_text(private_pem)
        pub_path.write_text(public_pem)

        from server.app import create_app
        from config.loader import AppConfig

        config = AppConfig(
            mcp={
                "enabled": False,
                "oauth": {
                    "jwt_secret": "rsa-test-secret",
                    "private_key_path": str(priv_path),
                    "public_key_path": str(pub_path),
                    "kid": "discovery-test-key",
                },
            },
        )
        app = create_app(config=config)
        with TestClient(app) as client:
            resp = client.get("/auth/.well-known/oauth-authorization-server")
            assert resp.status_code == 200
            body = resp.json()
            assert "jwks_uri" in body
            assert body["jwks_uri"].endswith("/.well-known/jwks.json")
            assert "RS256" in body.get("id_token_signing_alg_values_supported", [])


# ---------------------------------------------------------------------------
# Testes: issue_token e validate_token com RS256
# ---------------------------------------------------------------------------

class TestRS256TokenFlow:
    """Testes para emissão e validação de tokens RS256 via auth/jwt_validator.py."""

    @pytest.mark.unit
    def test_issue_and_validate_rs256(
        self, private_pem: str, public_pem: str
    ) -> None:
        from auth.jwt_validator import issue_token, validate_token

        token = issue_token(
            agent_id="partnera-agent",
            scopes=["aiops:diagnose"],
            secret=private_pem,
            ttl=60,
            algorithm="RS256",
        )
        claims = validate_token(token, public_pem, algorithm="RS256")
        assert claims.sub == "partnera-agent"
        assert "aiops:diagnose" in claims.scopes

    @pytest.mark.unit
    def test_rs256_token_rejected_with_wrong_public_key(
        self, private_pem: str
    ) -> None:
        from auth.jwt_validator import issue_token

        token = issue_token(
            agent_id="agent-x",
            scopes=[],
            secret=private_pem,
            algorithm="RS256",
        )
        # Chave pública diferente — deve rejeitar
        _, other_public_pem = generate_rsa_keypair(key_size=2048)
        with pytest.raises(pyjwt.InvalidTokenError):
            pyjwt.decode(token, other_public_pem, algorithms=["RS256"])
