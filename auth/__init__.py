"""Módulo de autenticação OAuth 2.1 com PKCE para o AIOps Agent."""
from __future__ import annotations

from auth.jwt_validator import TokenClaims, issue_token, validate_token
from auth.pkce import derive_challenge, generate_verifier, verify_challenge
from auth.scopes import TOOL_SCOPES, require_scope
from auth.token_store import InMemoryTokenStore, TokenStore

__all__ = [
    "TokenClaims",
    "issue_token",
    "validate_token",
    "generate_verifier",
    "derive_challenge",
    "verify_challenge",
    "TOOL_SCOPES",
    "require_scope",
    "TokenStore",
    "InMemoryTokenStore",
]
