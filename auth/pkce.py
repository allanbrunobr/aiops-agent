"""Geração e verificação de PKCE (Proof Key for Code Exchange) — RFC 7636.

Implementa o método S256: code_challenge = BASE64URL(SHA256(code_verifier)).
Funções puras sem estado — seguras para uso concorrente.
"""
from __future__ import annotations

import base64
import hashlib
import secrets


def generate_verifier(length: int = 64) -> str:
    """Gera um code_verifier criptograficamente seguro.

    Args:
        length: Número de bytes aleatórios (padrão 64 → 86 chars URL-safe).

    Returns:
        String URL-safe sem padding.
    """
    return secrets.token_urlsafe(length)


def derive_challenge(verifier: str) -> str:
    """Deriva code_challenge S256 a partir do code_verifier.

    Args:
        verifier: String gerada por generate_verifier().

    Returns:
        BASE64URL(SHA256(verifier)) sem padding '='.
    """
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def verify_challenge(verifier: str, challenge: str) -> bool:
    """Verifica que verifier corresponde ao challenge armazenado.

    Args:
        verifier: code_verifier original enviado pelo agente no token exchange.
        challenge: code_challenge armazenado durante o registro.

    Returns:
        True se o par verifier/challenge é válido.
    """
    return derive_challenge(verifier) == challenge
