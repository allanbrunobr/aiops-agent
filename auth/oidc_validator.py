"""Validação de tokens OIDC emitidos por IdPs externos (Phase 2 — Federation).

OIDCValidator valida Bearer tokens de provedores externos (ex: PartnerA, PartnerB)
sem compartilhar segredos — via JWKS discovery (RFC 7517 + RFC 8414 + OIDC Core).

Fluxo de validação:
  1. Decodifica token sem verificar → extrai 'iss'
  2. Verifica issuer contra lista de confiança (trusted_issuers)
  3. Busca JWKS via OpenID Configuration discovery (com TTL cache)
  4. Seleciona chave pública pelo 'kid' do header do token
  5. Valida assinatura RS256/ES256, exp, aud
  6. Mapeia claims para TokenClaims (sub, scopes, exp)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
import jwt as pyjwt
from jwt.algorithms import RSAAlgorithm

from auth.jwt_validator import TokenClaims


@dataclass(frozen=True)
class TrustedIssuerConfig:
    """Configuração de um provedor de identidade externo confiável."""

    issuer: str          # URL base do IdP (ex: "https://idp.partnera.example.com")
    audience: str        # aud esperado nos tokens deste IdP
    scope_claim: str = "scope"  # nome do claim que contém os scopes


class OIDCValidator:
    """Valida tokens OIDC de IdPs externos via JWKS discovery.

    Thread-safe para leituras concorrentes; _jwks_cache usa dict simples
    (Python GIL garante atomicidade de read/write em CPython).
    Para produção multi-processo, substituir por cache distribuído (Redis).
    """

    def __init__(
        self,
        trusted_issuers: list[TrustedIssuerConfig],
        cache_ttl: int = 3600,
        http_timeout: float = 10.0,
    ) -> None:
        """Inicializa o validador com a lista de issuers confiáveis.

        Args:
            trusted_issuers: Lista de configs de IdPs externos confiáveis.
            cache_ttl: TTL do cache JWKS em segundos (padrão 1h).
            http_timeout: Timeout para requisições HTTP ao IdP (padrão 10s).
        """
        self._trusted: dict[str, TrustedIssuerConfig] = {
            cfg.issuer: cfg for cfg in trusted_issuers
        }
        self._cache_ttl = cache_ttl
        self._http_timeout = http_timeout
        # issuer → (jwks_dict, expires_at_unix)
        self._jwks_cache: dict[str, tuple[dict, float]] = {}

    async def validate(self, token: str) -> TokenClaims:
        """Valida um token OIDC externo e retorna os claims.

        Args:
            token: Bearer token JWT (sem prefixo "Bearer ").

        Returns:
            TokenClaims com sub, scopes e exp.

        Raises:
            jwt.InvalidTokenError: Issuer não confiável, assinatura inválida,
                                   token expirado ou audience incorreta.
            httpx.HTTPError: Falha ao buscar JWKS do IdP externo.
        """
        # 1. Extrai issuer sem verificar assinatura
        try:
            unverified = pyjwt.decode(
                token,
                options={"verify_signature": False},
                algorithms=["RS256", "ES256"],
            )
        except pyjwt.DecodeError as exc:
            raise pyjwt.InvalidTokenError(f"Token malformado: {exc}") from exc

        iss = unverified.get("iss", "")
        if iss not in self._trusted:
            raise pyjwt.InvalidTokenError(f"Issuer não confiável: '{iss}'")

        cfg = self._trusted[iss]

        # 2. Busca JWKS com cache TTL
        jwks = await self._fetch_jwks(iss)

        # 3. Seleciona chave pelo kid
        header = pyjwt.get_unverified_header(token)
        kid = header.get("kid")
        public_key = self._find_key(jwks, kid)

        # 4. Valida assinatura, exp, aud
        payload = pyjwt.decode(
            token,
            public_key,
            algorithms=["RS256", "ES256"],
            audience=cfg.audience,
            issuer=iss,
        )

        # 5. Mapeia para TokenClaims
        raw_scope = payload.get(cfg.scope_claim, "")
        scopes = raw_scope.split() if raw_scope else []

        return TokenClaims(
            sub=payload["sub"],
            scopes=scopes,
            exp=int(payload.get("exp", 0)),
        )

    async def _fetch_jwks(self, issuer: str) -> dict:
        """Busca JWKS do IdP via OpenID Configuration discovery, com cache TTL.

        Args:
            issuer: URL base do IdP (sem trailing slash).

        Returns:
            JWKS dict com campo 'keys'.
        """
        now = time.time()
        cached = self._jwks_cache.get(issuer)
        if cached is not None:
            jwks, expires_at = cached
            if now < expires_at:
                return jwks

        base = issuer.rstrip("/")
        discovery_url = f"{base}/.well-known/openid-configuration"

        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.get(discovery_url)
            resp.raise_for_status()
            discovery = resp.json()

            jwks_uri = discovery.get("jwks_uri")
            if not jwks_uri:
                raise pyjwt.InvalidTokenError(
                    f"IdP '{issuer}' não publicou jwks_uri no discovery"
                )

            resp2 = await client.get(jwks_uri)
            resp2.raise_for_status()
            jwks = resp2.json()

        self._jwks_cache[issuer] = (jwks, now + self._cache_ttl)
        return jwks

    def _find_key(self, jwks: dict, kid: str | None) -> object:
        """Seleciona a chave pública correta do JWKS pelo kid.

        Args:
            jwks: JWKS dict com campo 'keys'.
            kid: Key ID do header do token (pode ser None).

        Returns:
            Objeto de chave pública compatível com PyJWT.

        Raises:
            jwt.InvalidTokenError: Nenhuma chave correspondente no JWKS.
        """
        keys = jwks.get("keys", [])
        if not keys:
            raise pyjwt.InvalidTokenError("JWKS não contém chaves")

        if kid:
            for k in keys:
                if k.get("kid") == kid:
                    return RSAAlgorithm.from_jwk(k)
            # kid não encontrado — tenta primeira chave como fallback
        return RSAAlgorithm.from_jwk(keys[0])

    def invalidate_cache(self, issuer: str | None = None) -> None:
        """Invalida cache JWKS (útil em rotação de chaves).

        Args:
            issuer: Issuer específico para invalidar. None = invalida todo o cache.
        """
        if issuer is None:
            self._jwks_cache.clear()
        else:
            self._jwks_cache.pop(issuer, None)
