"""OIDC: rotação de chave e claims fora do formato.

P2 — 4B-13 (docs/analise-20261109.md):
- `kid` desconhecido caía em keys[0] do JWKS em cache, sem buscar de novo:
  depois de uma rotação de chave no IdP, todo token novo era recusado até o
  cache expirar (até 1 h);
- claims fora do formato (sem `sub`) viravam KeyError → 503 "IdP indisponível".
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import jwt as pyjwt
import pytest

from auth.keys import generate_rsa_keypair, public_key_to_jwk
from tests.test_oidc_validator import (
    AUDIENCE,
    ISSUER,
    _make_discovery,
    _make_token,
    _make_validator,
    _mock_http_responses,
)

OLD_PRIV, OLD_PUB = generate_rsa_keypair(key_size=2048)
NEW_PRIV, NEW_PUB = generate_rsa_keypair(key_size=2048)
OLD_JWKS = {"keys": [public_key_to_jwk(OLD_PUB, kid="old")]}
NEW_JWKS = {"keys": [public_key_to_jwk(NEW_PUB, kid="new")]}


def _clients(*jwks_list):
    return [_mock_http_responses(_make_discovery(), jwks) for jwks in jwks_list]


@pytest.mark.unit
class TestKeyRotation:
    def test_token_with_new_kid_is_accepted_after_refetch(self):
        validator = _make_validator()
        clients = _clients(OLD_JWKS, NEW_JWKS)
        with patch("httpx.AsyncClient", side_effect=clients):
            asyncio.run(validator.validate(_make_token(OLD_PRIV, kid="old")))  # aquece o cache
            claims = asyncio.run(validator.validate(_make_token(NEW_PRIV, kid="new")))
        assert claims.sub == "assistant-bot"

    def test_unknown_kid_is_rejected_not_checked_with_another_key(self):
        validator = _make_validator()
        with patch("httpx.AsyncClient", side_effect=_clients(OLD_JWKS, OLD_JWKS)):
            asyncio.run(validator.validate(_make_token(OLD_PRIV, kid="old")))
            with pytest.raises(pyjwt.InvalidTokenError, match="kid"):
                asyncio.run(validator.validate(_make_token(NEW_PRIV, kid="ghost")))

    def test_refetch_for_unknown_kid_is_rate_limited(self):
        """Tokens com kid aleatório não podem forçar uma busca no IdP cada um."""
        validator = _make_validator()
        factory = patch("httpx.AsyncClient", side_effect=_clients(OLD_JWKS, OLD_JWKS, OLD_JWKS))
        with factory as mocked:
            asyncio.run(validator.validate(_make_token(OLD_PRIV, kid="old")))
            for kid in ("ghost-1", "ghost-2", "ghost-3"):
                with pytest.raises(pyjwt.InvalidTokenError):
                    asyncio.run(validator.validate(_make_token(NEW_PRIV, kid=kid)))
        assert mocked.call_count == 2  # carga inicial + 1 nova busca


@pytest.mark.unit
class TestMalformedClaims:
    def test_missing_sub_is_invalid_token_not_key_error(self):
        validator = _make_validator()
        token = pyjwt.encode(
            {"iss": ISSUER, "aud": AUDIENCE, "exp": 9_999_999_999, "scope": "aiops:search"},
            OLD_PRIV, algorithm="RS256", headers={"kid": "old"},
        )
        with patch("httpx.AsyncClient", side_effect=_clients(OLD_JWKS)):
            with pytest.raises(pyjwt.InvalidTokenError):
                asyncio.run(validator.validate(token))

    def test_scope_as_list_is_accepted(self):
        validator = _make_validator()
        token = pyjwt.encode(
            {"sub": "bot", "iss": ISSUER, "aud": AUDIENCE, "exp": 9_999_999_999,
             "scope": ["aiops:search", "aiops:diagnose"]},
            OLD_PRIV, algorithm="RS256", headers={"kid": "old"},
        )
        with patch("httpx.AsyncClient", side_effect=_clients(OLD_JWKS)):
            claims = asyncio.run(validator.validate(token))
        assert claims.scopes == ["aiops:search", "aiops:diagnose"]
