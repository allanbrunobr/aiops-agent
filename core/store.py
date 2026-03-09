"""Implementacoes de IncidentStore."""
from __future__ import annotations

from typing import Any


class InMemoryStore:
    """Store em memoria para testes e modo CLI."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def save(self, incident_id: str, state: Any) -> None:
        self._data[incident_id] = state

    def load(self, incident_id: str) -> Any | None:
        return self._data.get(incident_id)

    def list_incidents(self) -> list[str]:
        return list(self._data.keys())
