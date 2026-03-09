from __future__ import annotations

from typing import runtime_checkable

from typing import Protocol

from core.models import IncidentState


@runtime_checkable
class IncidentStore(Protocol):
    """Protocolo para armazenamento de estados de incidente."""

    def save(self, incident_id: str, state: IncidentState) -> None:
        """Persiste um estado de incidente."""
        ...

    def get(self, incident_id: str) -> IncidentState | None:
        """Recupera um estado de incidente por ID."""
        ...

    def list_active(self) -> list[tuple[str, IncidentState]]:
        """Lista todos os incidentes em estados não-terminais."""
        ...


@runtime_checkable
class CredentialProvider(Protocol):
    """Protocolo para provedor de credenciais."""

    def get_secret(self, key: str) -> str | None:
        """Recupera um segredo por chave."""
        ...
