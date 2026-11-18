"""Pós-revisão (menores): o erro ``invalid_token`` do MCP SSE *federado* deve ser
JSON válido.

``interfaces/mcp/sse.py`` (ramo ``auth_method="federated"``, ``_resolve_principal``)
interpolava ``{exc}`` cru numa string JSON manual
(``f'{{"error":"invalid_token","detail":"{exc}"}}'``). Uma mensagem de exceção com
aspas, barra invertida ou newline produzia JSON inválido. O fix usa ``json.dumps``.
(O ramo oauth21 usa detalhe estático "JWT is invalid" — já seguro, não interpola.)

Este teste força ``OIDCValidator.validate`` a lançar ``InvalidTokenError`` com
caracteres especiais e exige que a resposta 401 seja JSON parseável.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.oidc_validator import OIDCValidator, TrustedIssuerConfig

_PING = '{"jsonrpc":"2.0","id":1,"method":"ping"}'


class _OAuthCfg:
    jwt_secret = ""
    trusted_issuers = [
        TrustedIssuerConfig(issuer="https://idp.partner.test", audience="aiops-mcp")
    ]


class _McpCfg:
    auth_method = "federated"
    enabled_tools: list = []
    rate_limits: dict = {}
    oauth = _OAuthCfg()


@pytest.mark.unit
def test_federated_invalid_token_detail_is_valid_json(monkeypatch):
    nasty = 'bad "quote" \\ slash\nnewline\ttab'

    async def _boom(self, token):  # noqa: ANN001
        raise pyjwt.InvalidTokenError(nasty)

    monkeypatch.setattr(OIDCValidator, "validate", _boom)

    app = FastAPI()
    with patch("interfaces.mcp.sse.create_server", return_value=MagicMock()):
        from interfaces.mcp.sse import mount_sse

        mount_sse(app, config=_McpCfg())

    tc = TestClient(app, raise_server_exceptions=False)
    resp = tc.post(
        f"/mcp/messages?session_id={uuid.uuid4().hex}",
        headers={
            "Authorization": "Bearer whatever",
            "Content-Type": "application/json",
        },
        content=_PING,
    )
    assert resp.status_code == 401
    body = resp.json()  # antes do fix: JSONDecodeError (aspas/newline crus)
    assert body["error"] == "invalid_token"
    assert body["detail"] == nasty
