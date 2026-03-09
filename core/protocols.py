"""Protocolos (interfaces) para injecao de dependencia.

Define IncidentStore e CredentialProvider como Protocols (PEP 544)
para structural subtyping. Decorados com @runtime_checkable para
validacao no composition root via isinstance().
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from core.models import IncidentState


@runtime_checkable
class IncidentStore(Protocol):
    """Protocolo para persistencia de estado de incidentes."""

    def save(self, incident_id: str, state: IncidentState) -> None:
        """Armazena ou atualiza estado de um incidente."""
        ...

    def get(self, incident_id: str) -> IncidentState | None:
        """Recupera estado por ID. Retorna None se nao encontrado."""
        ...

    def list_active(self) -> list[tuple[str, IncidentState]]:
        """Lista incidentes ativos (nao-terminais) como (id, state)."""
        ...


@runtime_checkable
class CredentialProvider(Protocol):
    """Protocolo para recuperacao de credenciais."""

    def get(self, key: str) -> str | None:
        """Recupera credencial por chave. Retorna None se nao encontrada."""
        ...
