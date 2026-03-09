"""Carregador de configuracao Pydantic Settings para o AIOps Agent.

Encapsula ThresholdConfig existente e adiciona SafetyConfig
para uso pelo Blueprint Engine e adapters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from config_legacy import ThresholdConfig, load_config as _legacy_load


@dataclass
class SafetyConfig:
    """Configuracao de safety layers."""
    blast_radius_limit: int = 3
    require_human_approval: bool = True
    allow_ppa: bool = False


@dataclass
class AppConfig:
    """Configuracao completa da aplicacao.

    Combina thresholds do pipeline com configuracoes de safety.
    """
    thresholds: ThresholdConfig = field(default_factory=ThresholdConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)


def load_config(config_path: str | None = None) -> AppConfig:
    """Carrega configuracao completa.

    Args:
        config_path: Caminho para arquivo TOML (opcional).

    Returns:
        AppConfig com thresholds e safety config.
    """
    thresholds = _legacy_load(config_path)
    return AppConfig(thresholds=thresholds)
