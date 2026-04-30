"""Testes de integração ponta-a-ponta para auth_method="federated".

Simula um agente externo com IdP próprio (PartnerA, PartnerB) acessando
o endpoint SSE do AIOps Agent com um Bearer token RS256 — sem rede real.

Fluxo testado:
  1. IdP externo emite token RS256 assinado com sua chave privada
  2. Agente externo chama GET /mcp/sse com Bearer <token>
  3. OIDCValidator busca JWKS do IdP (mockado) e valida assinatura
  4. Auth layer extrai sub + scopes e repassa ao SSE handler
  5. SSE stream é estabelecido (transport mockado para não bloquear)

Cenários:
  - Sucesso: token RS256 válido de issuer confiável → 200 SSE
  - Sem Bearer: → 401
  - Issuer não confiável: → 401
  - Token expirado: → 401
  - Token com assinatura corrompida: → 401
  - Audience incorreta: → 401
  - IdP indisponível (HTTP 500): → 503
  - Cache JWKS: segundo request não re-busca JWKS
  - Múltiplos issuers: dois IdPs confiáveis independentes
"""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.keys import generate_rsa_keypair, public_key_to_jwk
from auth.oidc_validator import OIDCValidator, TrustedIssuerConfig


# ---------------------------------------------------------------------------
# Constantes e fixtures de módulo (geração RSA é lenta — roda uma vez)
# ---------------------------------------------------------------------------

_ISSUER_PARTNERA = "https://idp.partnera.test"
_ISSUER_PARTNERB = "https://idp.partnerb.test"
_AUDIENCE = "aiops-mcp"


@pytest.fixture(scope="module")
def partnera_keypair() -> tuple[str, str]:
    return generate_rsa_keypair(key_size=2048)


@pytest.fixture(scope="module")
def partnerb_keypair() -> tuple[str, str]:
    return generate_rsa_keypair(key_size=2048)


@pytest.fixture(scope="module")
def partnera_jwks(partnera_keypair: tuple[str, str]) -> dict:
    _, pub = partnera_keypair
    return {"keys": [public_key_to_jwk(pub, kid="partnera-key-1")]}


@pytest.fixture(scope="module")
def partnerb_jwks(partnerb_keypair: tuple[str, str]) -> dict:
    _, pub = partnerb_keypair
    return {"keys": [public_key_to_jwk(pub, kid="partnerb-key-1")]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_token(
    private_pem: str,
    iss: str = _ISSUER_PARTNERA,
    sub: str = "assistant-bot",
    aud: str = _AUDIENCE,
    scopes: str = "aiops:diagnose aiops:search",
    exp_offset: int = 300,
    kid: str = "partnera-key-1",
) -> str:
    return pyjwt.encode(
        {
            "sub": sub,
            "iss": iss,
            "aud": aud,
            "exp": int(time.time()) + exp_offset,
            "iat": int(time.time()),
            "scope": scopes,
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )


def _make_discovery(issuer: str) -> dict:
    return {
        "issuer": issuer,
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
    }


def _mock_idp_client(discovery: dict, jwks: dict) -> MagicMock:
    """Mock do httpx.AsyncClient que responde ao discovery e jwks do IdP."""
    disc_resp = MagicMock()
    disc_resp.json.return_value = discovery
    disc_resp.raise_for_status = MagicMock()

    jwks_resp = MagicMock()
    jwks_resp.json.return_value = jwks
    jwks_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[disc_resp, jwks_resp])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _mock_idp_error_client(status_code: int = 500) -> MagicMock:
    """Mock de IdP que retorna erro HTTP ao discovery."""
    err_resp = MagicMock()
    err_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(),
            response=MagicMock(status_code=status_code),
        )
    )

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=err_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


# ---------------------------------------------------------------------------
# Factory de app com auth_method="federated"
# ---------------------------------------------------------------------------

class _MockMcpConfig:
    """McpConfig mínimo para mount_sse() com auth_method=federated."""

    def __init__(self, trusted_issuers: list[TrustedIssuerConfig]) -> None:
        self.auth_method = "federated"
        self.oauth = _MockOAuthCfg(trusted_issuers)
        self.enabled_tools = []
        self.rate_limits: dict = {}


class _MockOAuthCfg:
    def __init__(self, trusted_issuers: list[TrustedIssuerConfig]) -> None:
        self.jwt_secret = ""
        self.trusted_issuers = trusted_issuers


@asynccontextmanager
async def _noop_connect_sse(*_args: object, **_kwargs: object) -> AsyncIterator[tuple]:
    """Substitui SseServerTransport.connect_sse — não bloqueia, stream vazio."""
    yield (AsyncMock(), AsyncMock())


def _make_federated_app(
    trusted_issuers: list[TrustedIssuerConfig] | None = None,
) -> FastAPI:
    """Cria app FastAPI com SSE montado em modo federado."""
    if trusted_issuers is None:
        trusted_issuers = [
            TrustedIssuerConfig(issuer=_ISSUER_PARTNERA, audience=_AUDIENCE)
        ]

    app = FastAPI()
    config = _MockMcpConfig(trusted_issuers)

    # Patcha create_server e o run do MCP para não depender de infra real
    mock_mcp_server = MagicMock()
    mock_mcp_server.run = AsyncMock()
    mock_mcp_server.create_initialization_options = MagicMock(return_value={})

    with patch("interfaces.mcp.sse.create_server", return_value=mock_mcp_server):
        from interfaces.mcp.sse import mount_sse
        mount_sse(app, config=config)

    return app


# ---------------------------------------------------------------------------
# 1. Rejeições — retornam 401 antes de chegar ao SSE stream
# ---------------------------------------------------------------------------

class TestFederatedAuthRejections:
    """Token inválido ou ausente deve ser barrado antes do SSE stream."""

    @pytest.fixture(autouse=True)
    def app(self) -> None:
        self._app = _make_federated_app()

    @pytest.mark.integration
    def test_missing_bearer_returns_401(self) -> None:
        with TestClient(self._app, raise_server_exceptions=False) as client:
            resp = client.get("/mcp/sse")
        assert resp.status_code == 401
        assert "Bearer token required" in resp.text

    @pytest.mark.integration
    def test_non_bearer_scheme_returns_401(self) -> None:
        with TestClient(self._app, raise_server_exceptions=False) as client:
            resp = client.get("/mcp/sse", headers={"Authorization": "Basic abc123"})
        assert resp.status_code == 401

    @pytest.mark.integration
    def test_untrusted_issuer_returns_401(
        self, partnera_keypair: tuple[str, str]
    ) -> None:
        priv, _ = partnera_keypair
        token = _make_token(priv, iss="https://evil.attacker.com")
        with TestClient(self._app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/mcp/sse", headers={"Authorization": f"Bearer {token}"}
            )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"] in ("invalid_token", "unauthorized")

    @pytest.mark.integration
    def test_expired_token_returns_401(
        self, partnera_keypair: tuple[str, str], partnera_jwks: dict
    ) -> None:
        priv, _ = partnera_keypair
        token = _make_token(priv, exp_offset=-30)  # já expirou
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with TestClient(self._app, raise_server_exceptions=False) as client:
                resp = client.get(
                    "/mcp/sse", headers={"Authorization": f"Bearer {token}"}
                )
        assert resp.status_code == 401
        assert resp.json()["error"] == "token_expired"

    @pytest.mark.integration
    def test_wrong_audience_returns_401(
        self, partnera_keypair: tuple[str, str], partnera_jwks: dict
    ) -> None:
        priv, _ = partnera_keypair
        token = _make_token(priv, aud="other-service")
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with TestClient(self._app, raise_server_exceptions=False) as client:
                resp = client.get(
                    "/mcp/sse", headers={"Authorization": f"Bearer {token}"}
                )
        assert resp.status_code == 401
        assert resp.json()["error"] == "invalid_token"

    @pytest.mark.integration
    def test_corrupted_signature_returns_401(
        self, partnera_keypair: tuple[str, str], partnera_jwks: dict
    ) -> None:
        priv, _ = partnera_keypair
        token = _make_token(priv)
        parts = token.split(".")
        parts[2] = parts[2][:-4] + "XXXX"
        corrupted = ".".join(parts)
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with TestClient(self._app, raise_server_exceptions=False) as client:
                resp = client.get(
                    "/mcp/sse", headers={"Authorization": f"Bearer {corrupted}"}
                )
        assert resp.status_code == 401

    @pytest.mark.integration
    def test_malformed_jwt_returns_401(self) -> None:
        with TestClient(self._app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/mcp/sse", headers={"Authorization": "Bearer nao.e.um.jwt"}
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 2. IdP indisponível — deve retornar 503
# ---------------------------------------------------------------------------

class TestFederatedIdPUnavailable:
    """Quando o IdP externo está fora do ar, retorna 503."""

    @pytest.fixture(autouse=True)
    def app(self) -> None:
        self._app = _make_federated_app()

    @pytest.mark.integration
    def test_idp_http_error_returns_503(
        self, partnera_keypair: tuple[str, str]
    ) -> None:
        priv, _ = partnera_keypair
        token = _make_token(priv)
        mock_client = _mock_idp_error_client(status_code=500)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with TestClient(self._app, raise_server_exceptions=False) as client:
                resp = client.get(
                    "/mcp/sse", headers={"Authorization": f"Bearer {token}"}
                )
        assert resp.status_code == 503
        assert resp.json()["error"] == "idp_unavailable"

    @pytest.mark.integration
    def test_idp_connection_error_returns_503(
        self, partnera_keypair: tuple[str, str]
    ) -> None:
        priv, _ = partnera_keypair
        token = _make_token(priv)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with TestClient(self._app, raise_server_exceptions=False) as client:
                resp = client.get(
                    "/mcp/sse", headers={"Authorization": f"Bearer {token}"}
                )
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 3. Sucesso — token válido chega ao SSE stream
# ---------------------------------------------------------------------------

class TestFederatedAuthSuccess:
    """Token RS256 válido de issuer confiável deve chegar ao SSE stream."""

    @pytest.mark.integration
    def test_valid_token_reaches_sse_stream(
        self, partnera_keypair: tuple[str, str], partnera_jwks: dict
    ) -> None:
        """Verifica que um token válido passa pelo auth layer e inicia o SSE."""
        priv, _ = partnera_keypair
        token = _make_token(priv, sub="assistant-bot")

        app = _make_federated_app()

        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        # Patcha connect_sse para responder imediatamente (sem bloquear)
        with patch("interfaces.mcp.sse._sse_transport") as mock_transport:
            mock_transport.connect_sse = _noop_connect_sse
            with patch("httpx.AsyncClient", return_value=mock_client):
                with TestClient(app, raise_server_exceptions=False) as client:
                    resp = client.get(
                        "/mcp/sse",
                        headers={"Authorization": f"Bearer {token}"},
                    )

        # Auth passou — chegou ao SSE handler (não 401)
        assert resp.status_code != 401
        assert resp.status_code != 403

    @pytest.mark.integration
    def test_valid_token_client_id_extracted(
        self, partnera_keypair: tuple[str, str], partnera_jwks: dict
    ) -> None:
        """sub do token deve ser propagado como client_id no ContextVar."""
        from interfaces.mcp.server import _current_client_id

        priv, _ = partnera_keypair
        token = _make_token(priv, sub="partnera-orchestrator")

        app = _make_federated_app()
        captured: list[str] = []

        @asynccontextmanager
        async def _capture_context(*_args: object, **_kw: object) -> AsyncIterator[tuple]:
            captured.append(_current_client_id.get())
            yield (AsyncMock(), AsyncMock())

        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("interfaces.mcp.sse._sse_transport") as mock_transport:
            mock_transport.connect_sse = _capture_context
            with patch("httpx.AsyncClient", return_value=mock_client):
                with TestClient(app, raise_server_exceptions=False) as client:
                    client.get(
                        "/mcp/sse",
                        headers={"Authorization": f"Bearer {token}"},
                    )

        assert captured == ["partnera-orchestrator"]

    @pytest.mark.integration
    def test_valid_token_scopes_extracted(
        self, partnera_keypair: tuple[str, str], partnera_jwks: dict
    ) -> None:
        """scope do token deve ser propagado como granted_scopes no ContextVar."""
        from interfaces.mcp.server import _current_scopes

        priv, _ = partnera_keypair
        token = _make_token(priv, scopes="aiops:diagnose aiops:validate")

        app = _make_federated_app()
        captured: list[list[str]] = []

        @asynccontextmanager
        async def _capture_scopes(*_args: object, **_kw: object) -> AsyncIterator[tuple]:
            captured.append(list(_current_scopes.get()))
            yield (AsyncMock(), AsyncMock())

        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("interfaces.mcp.sse._sse_transport") as mock_transport:
            mock_transport.connect_sse = _capture_scopes
            with patch("httpx.AsyncClient", return_value=mock_client):
                with TestClient(app, raise_server_exceptions=False) as client:
                    client.get(
                        "/mcp/sse",
                        headers={"Authorization": f"Bearer {token}"},
                    )

        assert captured == [["aiops:diagnose", "aiops:validate"]]


# ---------------------------------------------------------------------------
# 4. Cache JWKS — segundo request não refaz discovery
# ---------------------------------------------------------------------------

class TestFederatedJwksCache:
    """JWKS deve ser cacheado entre requests ao mesmo issuer."""

    @pytest.mark.integration
    def test_second_request_uses_jwks_cache(
        self, partnera_keypair: tuple[str, str], partnera_jwks: dict
    ) -> None:
        priv, _ = partnera_keypair
        token1 = _make_token(priv, sub="agent-1")
        token2 = _make_token(priv, sub="agent-2")

        app = _make_federated_app()

        # Primeiro request: 2 calls HTTP (discovery + jwks)
        m1 = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("interfaces.mcp.sse._sse_transport") as mock_transport:
            mock_transport.connect_sse = _noop_connect_sse
            with patch("httpx.AsyncClient", return_value=m1):
                with TestClient(app, raise_server_exceptions=False) as client:
                    client.get(
                        "/mcp/sse",
                        headers={"Authorization": f"Bearer {token1}"},
                    )

        # Segundo request: deve usar cache — httpx.AsyncClient NÃO deve ser chamado
        with patch("interfaces.mcp.sse._sse_transport") as mock_transport:
            mock_transport.connect_sse = _noop_connect_sse
            with patch("httpx.AsyncClient") as mock_cls:
                with TestClient(app, raise_server_exceptions=False) as client:
                    client.get(
                        "/mcp/sse",
                        headers={"Authorization": f"Bearer {token2}"},
                    )
                mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# 5. Múltiplos issuers — PartnerA e PartnerB independentes
# ---------------------------------------------------------------------------

class TestFederatedMultipleIssuers:
    """Dois IdPs confiáveis devem funcionar de forma independente."""

    @pytest.fixture(autouse=True)
    def multi_issuer_app(self) -> None:
        self._app = _make_federated_app(
            trusted_issuers=[
                TrustedIssuerConfig(issuer=_ISSUER_PARTNERA, audience=_AUDIENCE),
                TrustedIssuerConfig(issuer=_ISSUER_PARTNERB, audience=_AUDIENCE),
            ]
        )

    @pytest.mark.integration
    def test_partnera_token_accepted(
        self, partnera_keypair: tuple[str, str], partnera_jwks: dict
    ) -> None:
        priv, _ = partnera_keypair
        token = _make_token(priv, iss=_ISSUER_PARTNERA, kid="partnera-key-1")
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("interfaces.mcp.sse._sse_transport") as mock_transport:
            mock_transport.connect_sse = _noop_connect_sse
            with patch("httpx.AsyncClient", return_value=mock_client):
                with TestClient(self._app, raise_server_exceptions=False) as client:
                    resp = client.get(
                        "/mcp/sse",
                        headers={"Authorization": f"Bearer {token}"},
                    )
        assert resp.status_code != 401

    @pytest.mark.integration
    def test_partnerb_token_accepted(
        self, partnerb_keypair: tuple[str, str], partnerb_jwks: dict
    ) -> None:
        priv, _ = partnerb_keypair
        token = _make_token(
            priv, iss=_ISSUER_PARTNERB, kid="partnerb-key-1"
        )
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERB), partnerb_jwks)

        with patch("interfaces.mcp.sse._sse_transport") as mock_transport:
            mock_transport.connect_sse = _noop_connect_sse
            with patch("httpx.AsyncClient", return_value=mock_client):
                with TestClient(self._app, raise_server_exceptions=False) as client:
                    resp = client.get(
                        "/mcp/sse",
                        headers={"Authorization": f"Bearer {token}"},
                    )
        assert resp.status_code != 401

    @pytest.mark.integration
    def test_partnera_key_rejected_for_partnerb_issuer(
        self,
        partnera_keypair: tuple[str, str],
        partnerb_jwks: dict,  # chave pública da PartnerB, não da PartnerA
    ) -> None:
        """Token assinado pela PartnerA com issuer=partnerb deve falhar na validação."""
        partnera_priv, _ = partnera_keypair
        # Token diz que vem da PartnerB, mas foi assinado com a chave da PartnerA
        token = _make_token(partnera_priv, iss=_ISSUER_PARTNERB, kid="partnerb-key-1")
        # JWKS retorna a chave pública da PartnerB → assinatura não bate
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERB), partnerb_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with TestClient(self._app, raise_server_exceptions=False) as client:
                resp = client.get(
                    "/mcp/sse",
                    headers={"Authorization": f"Bearer {token}"},
                )
        assert resp.status_code == 401
