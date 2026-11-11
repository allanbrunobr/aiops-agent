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
from dataclasses import dataclass

import httpx
import jwt as pyjwt
from jwt.algorithms import RSAAlgorithm

from auth.jwt_validator import TokenClaims


# Janela mínima entre buscas forçadas do JWKS por kid desconhecido (por issuer).
FORCED_REFRESH_INTERVAL_SECONDS = 60.0


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
        # issuer → instante da última busca FORÇADA por kid desconhecido.
        self._last_forced_refresh: dict[str, float] = {}

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

        # 3. Seleciona chave pelo kid. kid desconhecido = provável rotação no
        # IdP: busca o JWKS de novo (no máximo 1x por janela, para que tokens
        # com kid aleatório não virem DoS contra o IdP). Nunca verifica com
        # outra chave (antes caía em keys[0] e recusava todo token novo).
        header = pyjwt.get_unverified_header(token)
        kid = header.get("kid")
        public_key = self._find_key(jwks, kid)
        if public_key is None and self._may_force_refresh(iss):
            self.invalidate_cache(iss)
            jwks = await self._fetch_jwks(iss)
            public_key = self._find_key(jwks, kid)
        if public_key is None:
            raise pyjwt.InvalidTokenError(f"Unknown signing key (kid={kid!r}) for issuer '{iss}'")

        # 4. Valida assinatura, exp, aud
        payload = pyjwt.decode(
            token,
            public_key,
            algorithms=["RS256", "ES256"],
            audience=cfg.audience,
            issuer=iss,
        )

        # 5. Mapeia para TokenClaims. Claim fora do formato é token inválido
        # (401), não erro interno (antes KeyError → 503 "IdP indisponível").
        sub = payload.get("sub")
        if not isinstance(sub, str) or not sub:
            raise pyjwt.InvalidTokenError("Token sem claim 'sub' válido")
        raw_scope = payload.get(cfg.scope_claim, "")
        if isinstance(raw_scope, list):
            scopes = [str(s) for s in raw_scope]
        elif isinstance(raw_scope, str):
            scopes = raw_scope.split()
        else:
            raise pyjwt.InvalidTokenError(f"Claim '{cfg.scope_claim}' em formato inválido")

        return TokenClaims(
            sub=sub,
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

    def _find_key(self, jwks: dict, kid: str | None) -> object | None:
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
            return None  # kid desconhecido: quem chama decide (nova busca ou recusa)
        if len(keys) == 1:
            return RSAAlgorithm.from_jwk(keys[0])
        raise pyjwt.InvalidTokenError("Token sem kid e JWKS com várias chaves")

    def _may_force_refresh(self, issuer: str) -> bool:
        """No máximo uma busca forçada por issuer a cada FORCED_REFRESH_INTERVAL."""
        now = time.time()
        last = self._last_forced_refresh.get(issuer, 0.0)
        if now - last < FORCED_REFRESH_INTERVAL_SECONDS:
            return False
        self._last_forced_refresh[issuer] = now
        return True

    def invalidate_cache(self, issuer: str | None = None) -> None:
        """Invalida cache JWKS (útil em rotação de chaves).

        Args:
            issuer: Issuer específico para invalidar. None = invalida todo o cache.
        """
        if issuer is None:
            self._jwks_cache.clear()
        else:
            self._jwks_cache.pop(issuer, None)
