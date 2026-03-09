"""
Configuração centralizada do AIOps Agent usando Pydantic Settings.

Carrega configuração de config/defaults.yaml com override por variáveis
de ambiente usando delimitador __ (ex: AIOPS__MCP__TRANSPORT=stdio).

História 1.1: Fundação de configuração com Pydantic Settings.
História 5.6: Configuração do servidor MCP via YAML.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


# Caminho padrão para arquivo YAML de configuração
_DEFAULTS_YAML = Path(__file__).parent / "defaults.yaml"


# ============================================================================
# Modelos de configuração aninhados
# ============================================================================


class ThresholdsConfig(BaseModel):
    """Thresholds de análise — migrados de config.py ThresholdConfig."""

    model_config = ConfigDict(frozen=True)

    sigma_threshold: float = 3.0
    baseline_ratio: float = 0.5
    percentile_threshold: float = 95.0
    max_hops: int = 3
    regression_threshold: float = -5.0  # STRATUS paper — não alterar sem justificativa
    error_threshold: int = 50
    detection_sigma_threshold: float = 3.0

    @field_validator("sigma_threshold", "detection_sigma_threshold")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        """Valida que threshold é positivo."""
        if v <= 0:
            raise ValueError(f"Must be > 0, got {v}")
        return v

    @field_validator("regression_threshold")
    @classmethod
    def must_be_non_positive(cls, v: float) -> float:
        """Valida que regression_threshold é não-positivo (design do paper STRATUS)."""
        if v > 0:
            raise ValueError(
                f"regression_threshold must be <= 0 (STRATUS design), got {v}"
            )
        return v


class SafetyConfig(BaseModel):
    """Configuração de segurança do pipeline."""

    model_config = ConfigDict(frozen=True)

    blast_radius_limit: int = 5
    require_human_approval: bool = True


class BlueprintConfig(BaseModel):
    """Configuração do Blueprint Engine (stub para futuros épicos)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False


class WebhookConfig(BaseModel):
    """Configuração do servidor webhook (stub para futuros épicos)."""

    model_config = ConfigDict(frozen=True)

    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1024, le=65535)
    secret: str = ""


class SlackConfig(BaseModel):
    """Configuração do Slack (stub para futuros épicos)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    webhook_url: str = ""


class McpRateLimitsConfig(BaseModel):
    """Limites de taxa por ferramenta MCP (chamadas/minuto)."""

    model_config = ConfigDict(frozen=True)

    diagnose: int = Field(default=10, ge=1, description="diagnose_incident calls/min")
    search: int = Field(default=60, ge=1, description="search_incidents calls/min")
    validate: int = Field(default=30, ge=1, description="validate_action calls/min")  # noqa: A003


class McpConfig(BaseModel):
    """Configuração do servidor MCP (Toolshed)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    transport: str = "sse"
    sse_port: int = Field(default=3001, ge=1024, le=65535)
    enabled_tools: list[str] = Field(
        default_factory=lambda: [
            "diagnose_incident",
            "validate_action",
            "search_incidents",
        ]
    )
    auth_method: str = "header"  # "header" (X-Client-Id) ou "none" (confiança implícita)
    rate_limits: McpRateLimitsConfig = Field(default_factory=McpRateLimitsConfig)

    @field_validator("transport")
    @classmethod
    def valid_transport(cls, v: str) -> str:
        """Valida que transporte é stdio ou sse."""
        if v not in ("stdio", "sse"):
            raise ValueError(f"transport must be 'stdio' or 'sse', got '{v}'")
        return v

    @field_validator("enabled_tools")
    @classmethod
    def valid_tools(cls, v: list[str]) -> list[str]:
        """Valida que todas as ferramentas estão no conjunto conhecido."""
        known = {"diagnose_incident", "validate_action", "search_incidents"}
        unknown = set(v) - known
        if unknown:
            raise ValueError(f"Unknown MCP tools: {unknown}. Known: {known}")
        return v


# ============================================================================
# AppConfig — raiz da configuração
# ============================================================================


class AppConfig(BaseSettings):
    """Configuração principal do AIOps Agent.

    Hierarquia de fontes (ordem de prioridade, maior primeiro):
      1. Variáveis de ambiente (AIOPS__SECAO__CAMPO=valor)
      2. Arquivo .env
      3. config/defaults.yaml
      4. Defaults dos modelos Pydantic

    Imutável após carregamento (frozen=True).
    """

    model_config = SettingsConfigDict(
        env_prefix="AIOPS__",
        env_nested_delimiter="__",
        frozen=True,
        extra="ignore",
        yaml_file=str(_DEFAULTS_YAML),
    )

    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    blueprint: BlueprintConfig = Field(default_factory=BlueprintConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)
    slack: SlackConfig = Field(default_factory=SlackConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Personaliza fontes de configuração: init > env > yaml > defaults."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
        )


# ============================================================================
# Função de carregamento de configuração
# ============================================================================


def load_config(config_path: str | None = None) -> AppConfig:
    """Carrega configuração do AIOps Agent.

    Se config_path for fornecido, carrega daquele arquivo YAML.
    Se None, usa config/defaults.yaml com overrides de variáveis de ambiente.

    Args:
        config_path: Caminho explícito para arquivo YAML (None = auto-discovery).

    Returns:
        AppConfig imutável com todos os valores validados.

    Raises:
        SystemExit: Se caminho explícito não existe ou valores são inválidos.
    """
    if config_path is not None:
        path = Path(config_path)
        if not path.exists():
            print(
                f"Error: Config file not found: {config_path}",
                file=sys.stderr,
            )
            raise SystemExit(1)

        # Reconfigura com yaml_file apontando para o caminho especificado
        try:
            from pydantic_settings import SettingsConfigDict

            class _CustomConfig(AppConfig):
                model_config = SettingsConfigDict(
                    env_prefix="AIOPS__",
                    env_nested_delimiter="__",
                    frozen=True,
                    extra="ignore",
                    yaml_file=str(path),
                )

                @classmethod
                def settings_customise_sources(
                    cls,
                    settings_cls: type[BaseSettings],
                    init_settings: PydanticBaseSettingsSource,
                    env_settings: PydanticBaseSettingsSource,
                    dotenv_settings: PydanticBaseSettingsSource,
                    file_secret_settings: PydanticBaseSettingsSource,
                ) -> tuple[PydanticBaseSettingsSource, ...]:
                    return (
                        init_settings,
                        env_settings,
                        dotenv_settings,
                        YamlConfigSettingsSource(settings_cls),
                    )

            return _CustomConfig()
        except Exception as e:
            print(f"Error: Invalid config in {config_path}: {e}", file=sys.stderr)
            raise SystemExit(1)

    try:
        return AppConfig()
    except Exception as e:
        print(f"Error: Invalid configuration: {e}", file=sys.stderr)
        raise SystemExit(1)
