"""Composição do monitor contínuo (VB3): diagnóstico profundo via V1.

Fica em app/ (raiz de composição) para o pipeline/ não importar builders.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

from domain.incident_state import IncidentState


def make_blueprint_diagnose(
    app_config: Any, *, no_llm: bool = True
) -> Callable[[IncidentState], IncidentState]:
    """Diagnóstico profundo via V1 ``BlueprintEngine.run`` (builders do app).

    Falha FECHADO: ``PipelineAborted`` (ex.: safety BLOCKED) vira estado sem
    execução; o monitor só consome proposta + postmortem.
    """

    def _diagnose(state: IncidentState) -> IncidentState:
        from adapters.credentials import EnvCredentialProvider
        from adapters.persistence.in_memory_store import InMemoryStore
        from app.builders import build_engine, build_pipeline

        engine = build_engine(app_config, InMemoryStore(), EnvCredentialProvider())
        return engine.run(
            build_pipeline(app_config), dataclasses.replace(state, no_llm=no_llm)
        )

    return _diagnose
