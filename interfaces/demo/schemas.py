from __future__ import annotations
"""
Schemas de serialização JSON para o módulo de demonstração.

TickFrame é o contrato principal entre o motor de simulação e
o dashboard via WebSocket. DemoPhase define as fases do estado.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel


class DemoPhase(str, Enum):
    """Fases da máquina de estado do cenário de demonstração."""
    IDLE = "IDLE"
    PLAYING = "PLAYING"
    DEGRADING = "DEGRADING"
    WARNING = "WARNING"
    ALERT = "ALERT"
    INCIDENT = "INCIDENT"
    RESOLVED = "RESOLVED"


class TickFrame(BaseModel):
    """Frame de atualização enviado ao dashboard a cada tick via WebSocket."""

    phase: str
    elapsed_sim_seconds: float
    sim_time: str  # ISO timestamp
    risk_score: float
    signals: dict[str, Any]
    prediction: dict[str, Any] | None
    recent_logs: list[dict[str, Any]]
    metrics: dict[str, float]
    scenario_name: str
    lead_time_seconds: float | None = None
