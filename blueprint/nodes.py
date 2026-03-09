"""Definicoes de tipos e dataclasses para nodes do pipeline Blueprint.

Define NodeType, OnFailure, NodeOutcome enums e BlueprintNode,
NodeResult, PipelineDefinition dataclasses — contrato para todas
as etapas do pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from core.models import IncidentState


class NodeType(Enum):
    """Tipo de node no pipeline Blueprint."""
    DETERMINISTIC = "deterministic"
    AGENTIC = "agentic"


class OnFailure(Enum):
    """Comportamento em caso de falha do node."""
    ABORT = "abort"       # Para o pipeline inteiro
    SKIP = "skip"         # Pula este node, continua
    FALLBACK = "fallback" # Tenta heuristica antes de abortar (so agentic)


class NodeOutcome(Enum):
    """Resultado da execucao de um node."""
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    FALLBACK_USED = "fallback_used"
    TIMEOUT = "timeout"
    ABORTED = "aborted"


@dataclass
class BlueprintNode:
    """Definicao de um node no pipeline Blueprint.

    Cada node encapsula uma etapa do pipeline com tipo explicito,
    callable de execucao, handler de falha, e timeout opcional.
    """
    id: str                                         # snake_case: "log_parser", "diagnoser"
    node_type: NodeType                             # DETERMINISTIC ou AGENTIC
    execute: Callable[[IncidentState], IncidentState]  # Funcao que processa o estado
    on_failure: OnFailure = OnFailure.ABORT         # Comportamento em caso de falha
    timeout_seconds: float | None = None            # Timeout individual do node (None = sem limite)
    fallback: Callable[[IncidentState], IncidentState] | None = None  # Heuristica p/ agentic nodes
    description: str = ""                           # Descricao para logs/debug


@dataclass
class NodeResult:
    """Resultado da execucao de um node individual."""
    node_id: str
    outcome: NodeOutcome
    duration_ms: float
    error: str | None = None
    llm_calls_used: int = 0


@dataclass
class PipelineDefinition:
    """Definicao completa de um pipeline Blueprint.

    Contem lista ordenada de nodes a executar sequencialmente.
    """
    name: str                          # "incident_response"
    nodes: list[BlueprintNode] = field(default_factory=list)
    description: str = ""
