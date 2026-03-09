"""Helpers de injecao de dependencia para FastAPI."""

from __future__ import annotations

from blueprint.engine import BlueprintEngine
from config.loader import AppConfig
from core.protocols import CredentialProvider, IncidentStore


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
