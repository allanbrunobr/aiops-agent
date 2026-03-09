from __future__ import annotations

from core.models import IncidentState

# Estados terminais do pipeline — incidentes nesses estados não aparecem em list_active()
_TERMINAL_STATES = {"RESOLVED", "FAILED", "ROLLBACK_COMPLETE"}


class InMemoryStore:
    """Implementação V1 do IncidentStore — armazenamento em memória."""

    def __init__(self) -> None:
        """Inicializa store vazio."""
        self._data: dict[str, IncidentState] = {}

    def save(self, incident_id: str, state: IncidentState) -> None:
        """Persiste um estado de incidente."""
        self._data[incident_id] = state

    def get(self, incident_id: str) -> IncidentState | None:
        """Recupera um estado de incidente por ID."""
        return self._data.get(incident_id)

    def list_active(self) -> list[tuple[str, IncidentState]]:
        """Lista incidentes em estados não-terminais."""
        return [
            (cid, state)
            for cid, state in self._data.items()
            if state.state not in _TERMINAL_STATES
        ]

    def list_all(self) -> list[tuple[str, IncidentState]]:
        """Lista TODOS os incidentes (ativos e terminais)."""
        return list(self._data.items())
