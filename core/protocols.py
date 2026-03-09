"""Protocolos (interfaces) para o AIOps Agent V1."""
from __future__ import annotations

from typing import Any, Protocol


class IncidentStore(Protocol):
    """Interface para persistencia de estado de incidentes."""

    def save(self, incident_id: str, state: Any) -> None: ...
    def load(self, incident_id: str) -> Any | None: ...
    def list_incidents(self) -> list[str]: ...


class CredentialProvider(Protocol):
    """Interface para obtencao de credenciais."""

    def get(self, key: str) -> str | None: ...
