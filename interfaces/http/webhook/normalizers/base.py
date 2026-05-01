from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from application.ports.credential_provider import CredentialProvider
from domain.alert import NormalizedAlert


class AlertNormalizer(ABC):
    """Classe base abstrata para normalizadores de alerta.

    Cada fonte de alerta (PagerDuty, Grafana, Databricks) implementa
    esta interface para converter payloads especificos em NormalizedAlert.
    """

    @property
    @abstractmethod
    def source_name(self) -> str:
        """Nome da fonte de alerta (e.g., 'pagerduty', 'grafana')."""
        ...

    @abstractmethod
    def validate_auth(
        self,
        headers: dict[str, str],
        body: bytes,
        credentials: CredentialProvider,
    ) -> bool:
        """Valida autenticacao da requisicao para esta fonte.

        Cada fonte tem sua propria estrategia de autenticacao:
        - PagerDuty: HMAC-SHA256
        - Grafana: Bearer token
        - Databricks: Managed Identity token

        Args:
            headers: Headers HTTP da requisicao (lowercase keys).
            body: Corpo bruto da requisicao (para verificacao HMAC).
            credentials: Provedor de credenciais para buscar segredos.

        Returns:
            True se autenticacao valida, False caso contrario.
        """
        ...

    @abstractmethod
    def normalize(self, payload: dict[str, Any]) -> NormalizedAlert:
        """Converte payload especifico da fonte em NormalizedAlert unificado.

        Args:
            payload: Dicionario JSON do payload recebido.

        Returns:
            NormalizedAlert com todos os campos obrigatorios preenchidos.

        Raises:
            ValueError: Se payload nao contem campos obrigatorios.
        """
        ...
