from __future__ import annotations

from typing import Any

import structlog

from server.webhook.normalizers.base import AlertNormalizer

log = structlog.get_logger()

# Registro global — preenchido pelos decorators ao importar modulos de normalizers
_registry: dict[str, AlertNormalizer] = {}


class NormalizerRegistry:
    """Registro central de normalizadores de alerta.

    Mantem mapeamento fonte -> instancia de normalizer.
    Preenchido automaticamente pelo decorator @register_normalizer.
    """

    @staticmethod
    def register(source: str, normalizer: AlertNormalizer) -> None:
        """Registra um normalizador para uma fonte.

        Args:
            source: Nome da fonte (e.g., 'pagerduty').
            normalizer: Instancia do normalizador.

        Raises:
            ValueError: Se a fonte ja esta registrada.
        """
        if source in _registry:
            raise ValueError(
                f"Duplicate normalizer registration for source '{source}'. "
                f"Already registered: {type(_registry[source]).__name__}"
            )
        _registry[source] = normalizer
        log.info("normalizer.registered", source=source, cls=type(normalizer).__name__)

    @staticmethod
    def get(source: str) -> AlertNormalizer | None:
        """Retorna normalizador para a fonte, ou None se nao registrado."""
        return _registry.get(source)

    @staticmethod
    def list_sources() -> list[str]:
        """Lista todas as fontes registradas."""
        return list(_registry.keys())

    @staticmethod
    def clear() -> None:
        """Limpa registro — uso exclusivo em testes."""
        _registry.clear()


def register_normalizer(source: str) -> Any:
    """Decorator que registra automaticamente um normalizador.

    Uso:
        @register_normalizer("pagerduty")
        class PagerDutyNormalizer(AlertNormalizer):
            ...

    O decorator instancia a classe e registra no NormalizerRegistry
    automaticamente ao importar o modulo.

    Args:
        source: Nome da fonte de alerta.

    Returns:
        Decorator que registra e retorna a classe original.
    """
    def decorator(cls: type[AlertNormalizer]) -> type[AlertNormalizer]:
        """Registra a classe no registry e retorna a classe inalterada."""
        instance = cls()
        NormalizerRegistry.register(source, instance)
        return cls
    return decorator
