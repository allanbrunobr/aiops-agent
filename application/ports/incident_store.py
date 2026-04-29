"""Porta ``IncidentStore`` — contrato de persistencia de estado de incidentes.

Define o Protocol consumido pela camada de aplicacao. Implementacoes
concretas vivem em ``adapters/persistence/``.

Decorado com ``@runtime_checkable`` para permitir validacao no
composition root via ``isinstance()``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from domain.incident_state import IncidentState


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
