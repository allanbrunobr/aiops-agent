"""
Transporte SSE do MCP Server para o AIOps Agent.

Monta endpoints SSE do MCP em uma aplicação FastAPI existente.
Autenticação via Bearer JWT (OAuth 2.1) ou header X-Client-Id (legado).

auth_method="oauth21"   — valida Bearer JWT local, extrai sub + scopes.
auth_method="federated" — valida Bearer JWT RS256 de IdPs externos via OIDC (Phase 2).
auth_method="header"    — X-Client-Id + X-Client-Secret obrigatórios (F4-2);
                          cliente sem secret configurado não autentica.
auth_method="none"      — sem autenticação (confiança implícita, dev only).

História 5.1: Fundação do servidor MCP com transporte dual.
Phase 2: auth_method="federated" com OIDCValidator e trusted_issuers.
Referência: architecture.md — MCP Transport Decision, Lines 334-341.
"""

from __future__ import annotations

import hmac

import jwt as pyjwt
from fastapi import FastAPI, Request, Response

from auth.jwt_validator import MIN_HMAC_KEY_BYTES, validate_token
from interfaces.mcp.server import (
    _current_client_id,
    _current_scopes,
    _enforce_scopes,
    create_server,
)
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.sse import SseServerTransport

# Estado do módulo — transporte e servidor MCP compartilhados
_sse_transport: SseServerTransport | None = None
_mcp_server = None


def _principal_user(client_id: str, scopes: list[str]) -> AuthenticatedUser:
    """Principal que o SseServerTransport usa para amarrar sessão ↔ cliente.

    O SDK compara `authorization_context(scope["user"])` de quem abriu a
    sessão (GET /mcp/sse) com o de quem envia cada POST /mcp/messages.
    Centralizado aqui para que GET, POST e testes construam o mesmo valor.
    """
    return AuthenticatedUser(
        AccessToken(token="", client_id=client_id, scopes=scopes, subject=client_id)
    )


def mount_sse(
    app: FastAPI,
    config: object | None = None,
    **server_deps: object,
) -> None:
    """Monta endpoints SSE do MCP no app FastAPI existente.

    Adiciona rotas:
      GET  /mcp/sse      — stream SSE para cliente
      POST /mcp/messages — mensagens MCP do cliente

    Autenticação configurável via config.auth_method:
      "oauth21" — Bearer JWT obrigatório (padrão recomendado)
      "header"  — X-Client-Id + X-Client-Secret (F4-2)
      "none"    — sem autenticação (dev/test only)

    Args:
        app: Aplicação FastAPI onde os endpoints serão montados.
        config: McpConfig com configurações (porta, auth_method, oauth, etc.).
        **server_deps: Dependências para create_server() (retriever, etc.).
    """
    global _sse_transport, _mcp_server

    _sse_transport = SseServerTransport("/mcp/messages")
    _mcp_server = create_server(config=config, **server_deps)

    auth_method = "header"
    jwt_secret = ""
    rsa_public_pem = ""
    issuer = "aiops-agent"
    allowed_header_clients: dict[str, dict] = {}
    _oidc_validator = None
    if config is not None:
        auth_method = getattr(config, "auth_method", "header")
        oauth_cfg = getattr(config, "oauth", None)
        if oauth_cfg is not None:
            jwt_secret = getattr(oauth_cfg, "jwt_secret", "") or ""
            issuer = getattr(oauth_cfg, "issuer", "aiops-agent") or "aiops-agent"
            # F4-3: quando o AS emite RS256, a validação usa a chave pública
            _pub_path = getattr(oauth_cfg, "public_key_path", "") or ""
            if _pub_path:
                from pathlib import Path as _Path

                if _Path(_pub_path).exists():
                    rsa_public_pem = _Path(_pub_path).read_text()
            for client in getattr(oauth_cfg, "allowed_clients", []) or []:
                if isinstance(client, dict):
                    client_id = client.get("id")
                    client_scopes = list(client.get("scopes") or [])
                    client_secret = str(client.get("secret") or "")
                else:
                    client_id = getattr(client, "id", None)
                    client_scopes = list(getattr(client, "scopes", []) or [])
                    client_secret = str(getattr(client, "secret", "") or "")
                if client_id:
                    allowed_header_clients[str(client_id)] = {
                        "scopes": client_scopes,
                        "secret": client_secret,
                    }

        # Phase 2 — inicializa OIDCValidator se auth_method="federated"
        if auth_method == "federated":
            import structlog as _structlog

            from auth.oidc_validator import OIDCValidator
            from auth.oidc_validator import TrustedIssuerConfig as _OIDCTrustedIssuerCfg

            _log = _structlog.get_logger()

            trusted_raw = getattr(oauth_cfg, "trusted_issuers", []) if oauth_cfg else []
            if not trusted_raw:
                _log.warning(
                    "oidc.no_trusted_issuers",
                    detail="auth_method=federated mas trusted_issuers está vazio — nenhum token externo será aceito",
                )
            _oidc_issuers = [
                _OIDCTrustedIssuerCfg(
                    issuer=getattr(ti, "issuer", ""),
                    audience=getattr(ti, "audience", ""),
                    scope_claim=getattr(ti, "scope_claim", "scope"),
                )
                for ti in trusted_raw
            ]
            _oidc_validator = OIDCValidator(trusted_issuers=_oidc_issuers)

    def _authenticate(request: Request) -> tuple[str, list[str], bool] | Response:
        """Autentica GET /mcp/sse e POST /mcp/messages com a mesma regra.

        Retorna (client_id, scopes, enforce_scopes) ou uma Response 401.
        Lista vazia de scopes com enforce=True não libera ferramentas.
        """
        if auth_method == "oauth21":
            if (
                not rsa_public_pem
                and len(jwt_secret.encode("utf-8")) < MIN_HMAC_KEY_BYTES
            ):
                return Response(
                    status_code=401,
                    content='{"error":"unauthorized","detail":"OAuth signing key is not configured"}',
                    media_type="application/json",
                )
            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                return Response(
                    status_code=401,
                    content='{"error":"unauthorized","detail":"Bearer token required"}',
                    media_type="application/json",
                )
            raw_token = auth_header[7:]
            try:
                # F4-3: audiência MCP obrigatória — token A2A não vale aqui
                claims = validate_token(
                    raw_token,
                    rsa_public_pem or jwt_secret,
                    algorithm="RS256" if rsa_public_pem else "HS256",
                    audience="mcp",
                    issuer=issuer,
                )
            except pyjwt.ExpiredSignatureError:
                return Response(
                    status_code=401,
                    content='{"error":"token_expired","detail":"JWT has expired"}',
                    media_type="application/json",
                )
            except pyjwt.InvalidTokenError:
                return Response(
                    status_code=401,
                    content='{"error":"invalid_token","detail":"JWT is invalid"}',
                    media_type="application/json",
                )
            return claims.sub, list(claims.scopes), True

        if auth_method == "header":
            client_id = request.headers.get("x-client-id")
            if not client_id or client_id not in allowed_header_clients:
                return Response(
                    status_code=401,
                    content="X-Client-Id header required",
                    media_type="text/plain",
                )
            entry = allowed_header_clients[client_id]
            expected_secret = entry["secret"]
            provided_secret = request.headers.get("x-client-secret", "")
            # F4-2: id publicado no YAML não basta — segredo obrigatório,
            # constant-time; cliente sem secret configurado não autentica
            if (
                not expected_secret
                or not provided_secret
                or not hmac.compare_digest(provided_secret, expected_secret)
            ):
                return Response(
                    status_code=401,
                    content="Invalid or missing X-Client-Secret",
                    media_type="text/plain",
                )
            return client_id, list(entry["scopes"]), True

        return "anonymous", [], False

    async def _resolve_principal(
        request: Request,
    ) -> tuple[str, list[str], bool] | Response:
        """Autentica GET /mcp/sse e POST /mcp/messages com a MESMA regra.

        Cobre todos os auth_method; o federated (OIDC, async) vive aqui para
        que GET e POST não dupliquem o fluxo nem divirjam nas respostas.
        """
        if auth_method != "federated":
            return _authenticate(request)

        # Phase 2 — OIDC Federation: Bearer JWT RS256 de IdP externo
        auth_header = request.headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            return Response(
                status_code=401,
                content='{"error":"unauthorized","detail":"Bearer token required"}',
                media_type="application/json",
            )
        try:
            claims = await _oidc_validator.validate(auth_header[7:])  # type: ignore[union-attr]
        except pyjwt.ExpiredSignatureError:
            return Response(
                status_code=401,
                content='{"error":"token_expired","detail":"JWT has expired"}',
                media_type="application/json",
            )
        except pyjwt.InvalidTokenError as exc:
            return Response(
                status_code=401,
                content=f'{{"error":"invalid_token","detail":"{exc}"}}',
                media_type="application/json",
            )
        except Exception:
            return Response(
                status_code=503,
                content='{"error":"idp_unavailable","detail":"Falha ao contactar IdP externo"}',
                media_type="application/json",
            )
        return claims.sub, list(claims.scopes), True

    def _bind_session_principal(
        request: Request, client_id: str, scopes: list[str]
    ) -> None:
        """F4-5: vincula a sessão SSE ao principal autenticado.

        SseServerTransport registra `scope["user"]` de quem abriu a sessão e
        rejeita (404, indistinguível de sessão inexistente) qualquer POST
        /mcp/messages cujo principal seja outro. Sem isto, um session_id
        vazado permitia que outro cliente autenticado injetasse mensagens.
        """
        request.scope["user"] = _principal_user(client_id, scopes)

    @app.get("/mcp/sse")
    async def sse_endpoint(request: Request) -> Response:
        """Endpoint SSE — cliente conecta aqui para stream de eventos MCP."""

        authenticated = await _resolve_principal(request)
        if isinstance(authenticated, Response):
            return authenticated
        effective_client_id, granted_scopes, enforce = authenticated
        # F4-5: a sessão criada por connect_sse fica amarrada a este principal
        _bind_session_principal(request, effective_client_id, granted_scopes)

        # Propaga client_id e scopes via ContextVar
        token_ctx = _current_client_id.set(effective_client_id)
        scopes_ctx = _current_scopes.set(granted_scopes)
        enforce_ctx = _enforce_scopes.set(enforce)
        try:
            async with _sse_transport.connect_sse(  # type: ignore[union-attr]
                request.scope, request.receive, request._send
            ) as streams:
                await _mcp_server.run(
                    streams[0],
                    streams[1],
                    _mcp_server.create_initialization_options(),
                )
        finally:
            _current_client_id.reset(token_ctx)
            _current_scopes.reset(scopes_ctx)
            _enforce_scopes.reset(enforce_ctx)

    @app.post("/mcp/messages")
    async def message_endpoint(request: Request) -> Response:
        """Endpoint para mensagens MCP do cliente (POST).

        Repete a autenticação do GET. Sem isso o POST aceitava qualquer corpo.
        F4-5: além de autenticado, o remetente precisa ser o MESMO principal
        que abriu a sessão — o transporte compara `scope["user"]` e responde
        404 em caso de mismatch (não revela que a sessão existe).
        """
        authenticated = await _resolve_principal(request)
        if isinstance(authenticated, Response):
            return authenticated
        client_id, scopes, _enforce = authenticated
        _bind_session_principal(request, client_id, scopes)
        await _sse_transport.handle_post_message(  # type: ignore[union-attr]
            request.scope, request.receive, request._send
        )
