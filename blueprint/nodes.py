"""Definicoes de tipos para o Blueprint Engine.

BlueprintNode, NodeType, OnFailure e PipelineDefinition.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class NodeType(Enum):
    """Tipo de node no pipeline."""
    DETERMINISTIC = "deterministic"
    AGENTIC = "agentic"


class OnFailure(Enum):
    """Comportamento em caso de falha do node."""
    ABORT = "abort"
    SKIP = "skip"
    FALLBACK = "fallback"


@dataclass
class BlueprintNode:
    """Um node no pipeline do Blueprint Engine."""
    id: str
    node_type: NodeType
    execute: Callable[..., Any]
    on_failure: OnFailure
    description: str = ""
    fallback: Callable[..., Any] | None = None


@dataclass
class NodeResult:
    """Resultado da execucao de um node."""
    node_id: str
    success: bool
    duration_ms: float = 0.0
    error: str | None = None
    used_fallback: bool = False


@dataclass
class PipelineDefinition:
    """Definicao completa de um pipeline."""
    name: str
    nodes: list[BlueprintNode] = field(default_factory=list)
    description: str = ""
