"""Implementacoes de CredentialProvider."""
from __future__ import annotations

import os


class EnvCredentialProvider:
    """Obtem credenciais de variaveis de ambiente."""

    def get(self, key: str) -> str | None:
        return os.environ.get(key)
