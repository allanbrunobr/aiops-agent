"""Porta de tracking de incidentes (ex.: Jira/PagerDuty).

Movida de tools/interfaces.py (A4); implementação em tools/jira_tracker.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class IncidentTracker(ABC):
    """Interface para tracking de incidentes (ex: Jira/PagerDuty via MCP)."""

    @abstractmethod
    def create_incident(self, title: str, severity: str, description: str) -> str:
        """Cria incidente e retorna ID."""
        ...

    @abstractmethod
    def create_postmortem(self, incident_id: str, timeline: list[dict],
                          root_cause: str, action_items: list[str]) -> str:
        """Cria documento de postmortem."""
        ...
