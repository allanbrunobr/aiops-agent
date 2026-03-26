"""Helpers de injecao de dependencia para FastAPI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from blueprint.engine import BlueprintEngine
from config.loader import AppConfig
from core.protocols import CredentialProvider, IncidentStore

if TYPE_CHECKING:
    from rag.knowledge_store import KnowledgeStore


def get_config() -> AppConfig:
    """Dependencia FastAPI: retorna configuracao da aplicacao."""
    from server.app import app_state
    assert app_state.config is not None, "Config not initialized"
    return app_state.config


def get_store() -> IncidentStore:
    """Dependencia FastAPI: retorna IncidentStore."""
    from server.app import app_state
    assert app_state.store is not None, "Store not initialized"
    return app_state.store


def get_engine() -> BlueprintEngine:
    """Dependencia FastAPI: retorna BlueprintEngine."""
    from server.app import app_state
    assert app_state.engine is not None, "Engine not initialized"
    return app_state.engine


def get_knowledge_store(request: object) -> KnowledgeStore | None:
    """Dependencia FastAPI: retorna KnowledgeStore via request.app.state."""
    from fastapi import Request
    if isinstance(request, Request):
        return request.app.state.knowledge_store  # type: ignore[attr-defined]
    from server.app import app_state
    return app_state.knowledge_store
