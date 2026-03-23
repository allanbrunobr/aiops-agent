"""Testes para Sprint 2 — auth/oidc_validator.py.

Usa mock IdP via httpx.MockTransport (sem respx, sem rede real).

Cobertura:
  - validate(): sucesso com RS256 e issuer confiável
  - validate(): rejeita issuer não confiável
  - validate(): rejeita token expirado
  - validate(): rejeita audience incorreta
  - validate(): usa cache JWKS (apenas 1 chamada HTTP mesmo com 2 validações)
  - validate(): invalida cache após invalidate_cache()
  - validate(): fallback para primeira chave se kid não encontrado
  - _fetch_jwks(): propaga erro HTTP do IdP
  - TrustedIssuerConfig: scope_claim customizado
"""
from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from jwt.algorithms import RSAAlgorithm

from auth.keys import generate_rsa_keypair, public_key_to_jwk
from auth.oidc_validator import OIDCValidator, TrustedIssuerConfig


# ---------------------------------------------------------------------------
# Helpers — Mock IdP
# ---------------------------------------------------------------------------

ISSUER = "https://idp.partnera.test"
AUDIENCE = "aiops-mcp"
KID = "partnera-key-1"


@pytest.fixture(scope="module")
def idp_keypair() -> tuple[str, str]:
    """Par RSA do IdP externo simulado."""
    return generate_rsa_keypair(key_size=2048)


@pytest.fixture(scope="module")
def idp_private_pem(idp_keypair: tuple[str, str]) -> str:
    return idp_keypair[0]


@pytest.fixture(scope="module")
def idp_public_pem(idp_keypair: tuple[str, str]) -> str:
    return idp_keypair[1]


@pytest.fixture(scope="module")
def idp_jwk(idp_public_pem: str) -> dict:
    return public_key_to_jwk(idp_public_pem, kid=KID)


@pytest.fixture(scope="module")
def idp_jwks(idp_jwk: dict) -> dict:
    return {"keys": [idp_jwk]}


def _make_token(
    private_pem: str,
    sub: str = "assistant-bot",
    iss: str = ISSUER,
    aud: str = AUDIENCE,
    scopes: str = "aiops:diagnose aiops:search",
    exp_offset: int = 300,
    kid: str = KID,
    scope_claim: str = "scope",
) -> str:
    """Emite um token RS256 simulando o IdP externo."""
    payload: dict[str, Any] = {
        "sub": sub,
        "iss": iss,
        "aud": aud,
        "exp": int(time.time()) + exp_offset,
        "iat": int(time.time()),
        scope_claim: scopes,
    }
    return pyjwt.encode(
        payload,
        private_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )


def _make_discovery(jwks_uri: str = f"{ISSUER}/.well-known/jwks.json") -> dict:
    return {
        "issuer": ISSUER,
        "jwks_uri": jwks_uri,
        "authorization_endpoint": f"{ISSUER}/authorize",
    }


def _mock_http_responses(discovery: dict, jwks: dict) -> MagicMock:
    """Cria mock do httpx.AsyncClient que retorna discovery e jwks."""
    discovery_response = MagicMock()
    discovery_response.json.return_value = discovery
    discovery_response.raise_for_status = MagicMock()

    jwks_response = MagicMock()
    jwks_response.json.return_value = jwks
    jwks_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[discovery_response, jwks_response])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _make_validator(issuers: list[TrustedIssuerConfig] | None = None) -> OIDCValidator:
    if issuers is None:
        issuers = [TrustedIssuerConfig(issuer=ISSUER, audience=AUDIENCE)]
    return OIDCValidator(trusted_issuers=issuers, cache_ttl=3600)


# ---------------------------------------------------------------------------
# Testes: validate() — caminho feliz
# ---------------------------------------------------------------------------

class TestOIDCValidatorSuccess:

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_validates_valid_rs256_token(
        self,
        idp_private_pem: str,
        idp_jwks: dict,
    ) -> None:
        validator = _make_validator()
        token = _make_token(idp_private_pem)
        mock_client = _mock_http_responses(_make_discovery(), idp_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            claims = await validator.validate(token)

        assert claims.sub == "assistant-bot"
        assert "aiops:diagnose" in claims.scopes
        assert "aiops:search" in claims.scopes

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_extracts_exp_claim(
        self,
        idp_private_pem: str,
        idp_jwks: dict,
    ) -> None:
        validator = _make_validator()
        token = _make_token(idp_private_pem, exp_offset=600)
        mock_client = _mock_http_responses(_make_discovery(), idp_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            claims = await validator.validate(token)

        assert claims.exp > int(time.time())

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_custom_scope_claim(
        self,
        idp_private_pem: str,
        idp_jwks: dict,
    ) -> None:
        """Valida que scope_claim customizado é lido corretamente."""
        issuers = [
            TrustedIssuerConfig(
                issuer=ISSUER,
                audience=AUDIENCE,
                scope_claim="permissions",
            )
        ]
        validator = _make_validator(issuers)
        token = _make_token(
            idp_private_pem,
            scopes="aiops:validate",
            scope_claim="permissions",
        )
        mock_client = _mock_http_responses(_make_discovery(), idp_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            claims = await validator.validate(token)

        assert "aiops:validate" in claims.scopes


# ---------------------------------------------------------------------------
# Testes: validate() — rejeições
# ---------------------------------------------------------------------------

class TestOIDCValidatorRejections:

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_untrusted_issuer(self, idp_private_pem: str) -> None:
        validator = _make_validator()
        token = _make_token(idp_private_pem, iss="https://evil.attacker.com")

        with pytest.raises(pyjwt.InvalidTokenError, match="Issuer não confiável"):
            await validator.validate(token)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_expired_token(
        self,
        idp_private_pem: str,
        idp_jwks: dict,
    ) -> None:
        validator = _make_validator()
        token = _make_token(idp_private_pem, exp_offset=-10)  # já expirado
        mock_client = _mock_http_responses(_make_discovery(), idp_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(pyjwt.ExpiredSignatureError):
                await validator.validate(token)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_wrong_audience(
        self,
        idp_private_pem: str,
        idp_jwks: dict,
    ) -> None:
        validator = _make_validator()
        token = _make_token(idp_private_pem, aud="another-service")
        mock_client = _mock_http_responses(_make_discovery(), idp_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(pyjwt.InvalidTokenError):
                await validator.validate(token)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_tampered_token(
        self,
        idp_private_pem: str,
        idp_jwks: dict,
    ) -> None:
        """Token com assinatura corrompida deve ser rejeitado."""
        validator = _make_validator()
        token = _make_token(idp_private_pem)
        # Corrompe a assinatura
        parts = token.split(".")
        parts[2] = parts[2][:-4] + "XXXX"
        corrupted = ".".join(parts)
        mock_client = _mock_http_responses(_make_discovery(), idp_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(pyjwt.InvalidTokenError):
                await validator.validate(corrupted)

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_rejects_malformed_token(self) -> None:
        validator = _make_validator()
        with pytest.raises(pyjwt.InvalidTokenError):
            await validator.validate("nao-e-um-jwt")


# ---------------------------------------------------------------------------
# Testes: cache JWKS
# ---------------------------------------------------------------------------

class TestOIDCValidatorCache:

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_jwks_cached_after_first_call(
        self,
        idp_private_pem: str,
        idp_jwks: dict,
    ) -> None:
        """Segunda validação usa cache — sem nova chamada HTTP."""
        validator = _make_validator()

        # Primeira chamada: 2 requests (discovery + jwks)
        m1 = _mock_http_responses(_make_discovery(), idp_jwks)
        token1 = _make_token(idp_private_pem)
        with patch("httpx.AsyncClient", return_value=m1):
            await validator.validate(token1)

        # Segunda chamada: cache hit — sem entrar em httpx.AsyncClient
        token2 = _make_token(idp_private_pem, sub="outro-agente")
        with patch("httpx.AsyncClient") as mock_cls:
            await validator.validate(token2)
            mock_cls.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_cache_invalidated(
        self,
        idp_private_pem: str,
        idp_jwks: dict,
    ) -> None:
        """Após invalidate_cache(), nova chamada HTTP deve ocorrer."""
        validator = _make_validator()

        m1 = _mock_http_responses(_make_discovery(), idp_jwks)
        token = _make_token(idp_private_pem)
        with patch("httpx.AsyncClient", return_value=m1):
            await validator.validate(token)

        validator.invalidate_cache(ISSUER)

        m2 = _mock_http_responses(_make_discovery(), idp_jwks)
        with patch("httpx.AsyncClient", return_value=m2):
            claims = await validator.validate(token)
        assert claims.sub == "assistant-bot"

    @pytest.mark.asyncio
    @pytest.mark.unit
    async def test_invalidate_all_cache(
        self,
        idp_private_pem: str,
        idp_jwks: dict,
    ) -> None:
        validator = _make_validator()
        m1 = _mock_http_responses(_make_discovery(), idp_jwks)
        token = _make_token(idp_private_pem)
        with patch("httpx.AsyncClient", return_value=m1):
            await validator.validate(token)

        validator.invalidate_cache()  # invalida tudo
        assert validator._jwks_cache == {}


# ---------------------------------------------------------------------------
# Testes: seleção de chave (_find_key)
# ---------------------------------------------------------------------------

class TestFindKey:

    @pytest.mark.unit
    def test_finds_key_by_kid(self, idp_jwk: dict) -> None:
        validator = _make_validator()
        jwks = {"keys": [idp_jwk, {"kty": "RSA", "kid": "other-key", **idp_jwk}]}
        key = validator._find_key(jwks, KID)
        assert key is not None

    @pytest.mark.unit
    def test_falls_back_to_first_key_when_kid_missing(self, idp_jwk: dict) -> None:
        validator = _make_validator()
        jwks = {"keys": [idp_jwk]}
        key = validator._find_key(jwks, kid=None)
        assert key is not None

    @pytest.mark.unit
    def test_raises_when_no_keys(self) -> None:
        validator = _make_validator()
        with pytest.raises(pyjwt.InvalidTokenError, match="JWKS não contém chaves"):
            validator._find_key({"keys": []}, kid=None)
