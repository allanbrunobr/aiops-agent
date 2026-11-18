"""Autenticação Bearer compartilhada para a superfície HTTP de gestão.

Correção pós-revisão #3: as rotas ``knowledge`` e ``admin`` eram montadas em
``create_app`` sem nenhuma autenticação — ``POST /api/v1/knowledge/patterns``
escrevia na base de conhecimento anonimamente e
``POST /api/v1/admin/validate/knowledge`` disparava FAISS+embeddings em thread
pool (vetor de DoS/escrita anônima). A revisão recomendou exigir auth (ou não
montar sem auth, postura do remoto).

Decisão: MANTER montadas (intenção F3-5 — as implementações reais existem) mas
exigir um Bearer JWT válido da mesma superfície de API autenticada do A2A
(``aud="a2a"``). Fail-closed: sem chave configurada, sem token ou token inválido
→ 401. O gate é aplicado no composition root (``create_app``) via
``dependencies=[Depends(require_api_bearer)]``; os testes de unidade do router
montam o router isoladamente e exercitam a lógica sem o gate.
"""

from __future__ import annotations

from typing import Any

import jwt as pyjwt
from fastapi import HTTPException, Request

from auth.jwt_validator import MIN_HMAC_KEY_BYTES, validate_token


def require_api_bearer(request: Request) -> Any:
    """Exige Bearer JWT (RS256 se houver par RSA, senão HS256), aud="a2a".

    Raises:
        HTTPException 401: auth não configurada, token ausente/inválido/expirado
            ou de audiência errada (ex.: token MCP não vale aqui).
    """
    public_pem = getattr(request.app.state, "a2a_jwt_public_pem", "") or ""
    secret = getattr(request.app.state, "a2a_jwt_secret", "") or ""
    if public_pem:
        key, algorithm = public_pem, "RS256"
    elif len(secret.encode("utf-8")) >= MIN_HMAC_KEY_BYTES:
        key, algorithm = secret, "HS256"
    else:
        raise HTTPException(
            status_code=401,
            detail="API authentication is not configured",
        )
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer ") or not header[7:].strip():
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        return validate_token(
            header[7:].strip(), key, algorithm=algorithm, audience="a2a"
        )
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="JWT has expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="JWT is invalid")


__all__ = ["require_api_bearer"]
