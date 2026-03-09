from __future__ import annotations

import os


class EnvCredentialProvider:
    """Provedor de credenciais via variaveis de ambiente.

    Recupera segredos do ambiente sem expor valores em logs ou configs.
    """

    def get(self, key: str) -> str | None:
        """Retorna o valor da variavel de ambiente ou None se ausente."""
        return os.environ.get(key)
