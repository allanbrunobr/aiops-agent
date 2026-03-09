from __future__ import annotations

import os


class EnvCredentialProvider:
    """Implementacao V1 do CredentialProvider — le de variaveis de ambiente."""

    def get(self, key: str) -> str | None:
        """Recupera credencial por chave de variavel de ambiente.

        Valores de credencial NUNCA sao logados.
        """
        return os.environ.get(key)
