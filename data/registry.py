"""
Registro de cenários de incidentes simulados.

Cada cenário é uma função que aceita um parâmetro `seed` (int) e retorna
um SimulatedIncident com dados determinísticos. O registry mapeia nomes
legíveis (e.g., "payment-pool-exhaustion") às respectivas funções geradoras.

Módulo stdlib-only — não importa dependências pesadas (torch, faiss, etc.).
"""
from __future__ import annotations

from typing import Callable

from data.models import SimulatedIncident

# Tipo para funções geradoras de cenário: recebem seed, retornam incidente.
ScenarioFn = Callable[[int], SimulatedIncident]

# Registry interno — populado via register_scenario().
_REGISTRY: dict[str, ScenarioFn] = {}


def register_scenario(name: str, fn: ScenarioFn) -> None:
    """Registra uma função geradora de cenário com o nome dado.

    Args:
        name: Identificador único do cenário (e.g., "payment-pool-exhaustion").
        fn: Callable que aceita seed (int) e retorna SimulatedIncident.

    Raises:
        ValueError: Se já existir um cenário registrado com o mesmo nome.
    """
    if name in _REGISTRY:
        raise ValueError(
            f"Cenário '{name}' já está registrado. "
            f"Use um nome diferente ou remova o registro existente."
        )
    _REGISTRY[name] = fn


def get_scenario(name: str) -> ScenarioFn:
    """Retorna a função geradora para o cenário especificado.

    Args:
        name: Nome do cenário registrado.

    Returns:
        Função geradora (ScenarioFn).

    Raises:
        KeyError: Se o cenário não estiver registrado, com lista dos
                  cenários disponíveis na mensagem de erro.
    """
    if name not in _REGISTRY:
        available = ", ".join(sorted(_REGISTRY.keys())) or "(nenhum registrado)"
        raise KeyError(
            f"Cenário '{name}' não encontrado. "
            f"Cenários disponíveis: {available}"
        )
    return _REGISTRY[name]


def list_scenarios() -> list[str]:
    """Retorna lista ordenada dos nomes de cenários registrados."""
    return sorted(_REGISTRY.keys())
