"""Geração e serialização de chaves RSA para o Authorization Server.

Sprint 1 — Phase 2 Agent SSO Federation.

Expõe:
  generate_rsa_keypair() → (private_key_pem, public_key_pem)
  public_key_to_jwk(public_key_pem, kid) → dict  — chave JWK (RFC 7517)
  load_or_generate_keypair(private_path, public_path, kid) → (str, str, dict)
"""
from __future__ import annotations

import base64
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def generate_rsa_keypair(key_size: int = 2048) -> tuple[str, str]:
    """Gera par de chaves RSA e retorna em formato PEM.

    Args:
        key_size: Tamanho da chave em bits (padrão 2048 — mínimo recomendado).

    Returns:
        Tupla (private_key_pem, public_key_pem) como strings PEM.
    """
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=key_size,
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return private_pem, public_pem


def public_key_to_jwk(public_key_pem: str, kid: str = "aiops-rs256-1") -> dict:
    """Converte chave pública RSA PEM para formato JWK (RFC 7517).

    Args:
        public_key_pem: Chave pública RSA em formato PEM.
        kid: Identificador da chave (key ID) para o campo 'kid' do JWK.

    Returns:
        Dict com campos kty, use, alg, kid, n, e no formato JWK.
    """
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    key = load_pem_public_key(public_key_pem.encode())
    pub_numbers = key.public_numbers()  # type: ignore[attr-defined]

    def _int_to_base64url(n: int) -> str:
        length = (n.bit_length() + 7) // 8
        raw = n.to_bytes(length, "big")
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": kid,
        "n": _int_to_base64url(pub_numbers.n),
        "e": _int_to_base64url(pub_numbers.e),
    }


def load_or_generate_keypair(
    private_path: str | Path,
    public_path: str | Path,
    kid: str = "aiops-rs256-1",
) -> tuple[str, str, dict]:
    """Carrega par de chaves de disco ou gera e salva se não existir.

    Útil no startup: se os arquivos existirem, carrega. Se não,
    gera um novo par e salva nos caminhos especificados.

    Args:
        private_path: Caminho para o arquivo da chave privada PEM.
        public_path: Caminho para o arquivo da chave pública PEM.
        kid: Identificador da chave para o JWK.

    Returns:
        Tupla (private_key_pem, public_key_pem, jwk_dict).
    """
    priv = Path(private_path)
    pub = Path(public_path)

    if priv.exists() and pub.exists():
        private_pem = priv.read_text()
        public_pem = pub.read_text()
    else:
        private_pem, public_pem = generate_rsa_keypair()
        priv.parent.mkdir(parents=True, exist_ok=True)
        pub.parent.mkdir(parents=True, exist_ok=True)
        priv.write_text(private_pem)
        pub.write_text(public_pem)

    jwk = public_key_to_jwk(public_pem, kid=kid)
    return private_pem, public_pem, jwk
