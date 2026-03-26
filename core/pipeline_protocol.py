"""Protocolo de pipeline do AIOps Agent — Story 4.2.

Define PipelineProtocol para que CLI (main.py) selecione a implementação
via factory sem condicionais inline espalhados pelo código.

V0: pipeline sequencial de funções step_*() em main.py
V1: Blueprint Engine (BlueprintEngine) em blueprint/engine.py
"""

from __future__ import annotations

import argparse
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class PipelineProtocol(Protocol):
    """Protocolo de execução do pipeline de resposta a incidentes.

    Ambas as implementações (V0 e V1) devem satisfazer esta interface.
    """

    def run(self, args: argparse.Namespace) -> int:
        """Executa o pipeline completo e retorna o exit code.

        Args:
            args: Namespace com flags de execução (no_llm, no_rag, etc.).

        Returns:
            Exit code do pipeline (0=success, 2=regression, 3=approval-needed, etc.).
        """
        ...


class CallablePipelineRunner:
    """Runner genérico que delega para uma callable.

    Permite que main.py injete a implementação concreta sem criar
    dependência circular em core/pipeline_protocol.py.
    """

    def __init__(self, impl: Callable[[argparse.Namespace], int]) -> None:
        """Inicializa com a implementação a delegar.

        Args:
            impl: Função (args: Namespace) → int que implementa o pipeline.
        """
        self._impl = impl

    def run(self, args: argparse.Namespace) -> int:
        """Executa o pipeline delegando para a implementação injetada."""
        return self._impl(args)


def make_pipeline(impl: Callable[[argparse.Namespace], int]) -> PipelineProtocol:
    """Factory helper: cria um PipelineProtocol a partir de uma callable.

    Args:
        impl: Função que implementa o pipeline.

    Returns:
        CallablePipelineRunner que satisfaz PipelineProtocol.
    """
    return CallablePipelineRunner(impl)
