"""Implementacao V1 do IncidentStore — armazenamento em memoria."""

from __future__ import annotations

from core.models import IncidentState

# Estados terminais — incidentes nestes estados nao aparecem em list_active()
_TERMINAL_STATES = {"RESOLVED", "CANCELLED", "FAILED"}


class InMemoryStore:
    """Implementacao V1 do IncidentStore — armazenamento em memoria."""

    def __init__(self) -> None:
        self._data: dict[str, IncidentState] = {}

    def save(self, incident_id: str, state: IncidentState) -> None:
        """Armazena ou atualiza estado de um incidente."""
        self._data[incident_id] = state

    def get(self, incident_id: str) -> IncidentState | None:
        """Recupera estado por ID. Retorna None se nao encontrado."""
        return self._data.get(incident_id)

    def list_active(self) -> list[tuple[str, IncidentState]]:
        """Lista incidentes ativos (nao-terminais)."""
        return [
            (iid, s) for iid, s in self._data.items()
            if s.state not in _TERMINAL_STATES
        ]

    def list_all(self) -> list[tuple[str, IncidentState]]:
        """Lista todos os incidentes, incluindo terminais."""
        return list(self._data.items())
