"""Modulo de configuracao centralizada com Pydantic Settings v2.

Carrega configuracao de YAML (config/defaults.yaml) com override por
variaveis de ambiente (prefixo AIOPS_, delimitador __).

Substitui o antigo config.py (stdlib tomllib) por Pydantic Settings
com validacao de tipos, imutabilidade e hierarquia de fontes.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    YamlConfigSettingsSource,
)


class ThresholdsConfig(BaseModel):
    """Thresholds de analise — migrados de config.py ThresholdConfig."""

    model_config = ConfigDict(frozen=True)

    sigma_threshold: float = 3.0
    baseline_ratio: float = 0.5
    percentile_threshold: float = 95.0
    max_hops: int = 3
    regression_threshold: float = -5.0  # STRATUS paper — do NOT change without justification
    error_threshold: int = 50
    detection_sigma_threshold: float = 3.0

    @field_validator("sigma_threshold", "detection_sigma_threshold")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        """Sigma thresholds devem ser positivos."""
        if v <= 0:
            raise ValueError(f"Must be > 0, got {v}")
        return v

    @field_validator("regression_threshold")
    @classmethod
    def must_be_non_positive(cls, v: float) -> float:
        """regression_threshold deve ser <= 0 (design STRATUS paper)."""
        if v > 0:
            raise ValueError(f"regression_threshold must be <= 0 (STRATUS design), got {v}")
        return v


class SafetyConfig(BaseModel):
    """Configuracao de seguranca do pipeline."""

    model_config = ConfigDict(frozen=True)

    blast_radius_limit: int = 5
    require_human_approval: bool = True


class BlueprintConfig(BaseModel):
    """Configuracao do Blueprint Engine — motor de orquestracao do pipeline."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    pipeline_timeout_seconds: int = 90   # NFR1 — timeout global do pipeline
    max_llm_calls: int = 15             # NFR6 — limite de chamadas LLM por run


class WebhookConfig(BaseModel):
    """Stub — preenchido em Story 2.1 (Alert Ingestion)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    port: int = 8000


class SlackConfig(BaseModel):
    """Configuracao do notificador Slack — Story 3.1 + 3.6."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    channel: str = ""
    max_retries: int = 3
    initial_backoff_s: float = 1.0
    backoff_multiplier: float = 2.0
    fallback_dir: str = "output"
    dry_run: bool = False

    @field_validator("channel")
    @classmethod
    def channel_required_when_enabled(cls, v: str, info: Any) -> str:
        """channel is required when enabled=True."""
        if info.data.get("enabled") and not v:
            raise ValueError("channel is required when Slack is enabled")
        return v

    @field_validator("max_retries")
    @classmethod
    def retries_non_negative(cls, v: int) -> int:
        """max_retries must be >= 0."""
        if v < 0:
            raise ValueError("must be >= 0")
        return v

    @field_validator("initial_backoff_s")
    @classmethod
    def backoff_positive(cls, v: float) -> float:
        """initial_backoff_s must be > 0."""
        if v <= 0:
            raise ValueError("must be > 0")
        return v


class ObservabilityConfig(BaseModel):
    """Configuracao de observabilidade — Story 3.6."""

    model_config = ConfigDict(frozen=True)

    metric_retention_hours: int = 24
    accuracy_tracking_enabled: bool = True

    @field_validator("metric_retention_hours")
    @classmethod
    def retention_positive(cls, v: int) -> int:
        """metric_retention_hours must be >= 1."""
        if v < 1:
            raise ValueError("must be >= 1")
        return v


class NotificationsConfig(BaseModel):
    """Configuracao de notificacoes — Story 3.6."""

    model_config = ConfigDict(frozen=True)

    slack: SlackConfig = SlackConfig()
    observability: ObservabilityConfig = ObservabilityConfig()


class AppConfig(BaseSettings):
    """Configuracao raiz do AIOps Agent."""

    model_config = SettingsConfigDict(
        yaml_file="config/defaults.yaml",
        yaml_file_encoding="utf-8",
        env_nested_delimiter="__",
        env_prefix="AIOPS_",
        frozen=True,
        extra="ignore",
    )

    thresholds: ThresholdsConfig = ThresholdsConfig()
    safety: SafetyConfig = SafetyConfig()
    blueprint: BlueprintConfig = BlueprintConfig()
    webhook: WebhookConfig = WebhookConfig()
    slack: SlackConfig = SlackConfig()
    notifications: NotificationsConfig = NotificationsConfig()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Hierarquia de fontes: init > env > dotenv > YAML > file_secret."""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            YamlConfigSettingsSource(settings_cls),
            file_secret_settings,
        )


def load_config(
    config_path: str | None = None,
    yaml_file: str | Path | None = None,
) -> AppConfig:
    """Carrega configuracao do sistema.

    Prioridade: init params > env vars > .env > YAML file > defaults.
    Se config_path ou yaml_file fornecido, usa aquele YAML em vez do defaults.yaml.
    """
    try:
        effective_path = yaml_file if yaml_file is not None else config_path
        if effective_path is not None:
            path = Path(effective_path)
            if not path.exists():
                print(f"Error: Config file not found: {effective_path}", file=sys.stderr)
                raise SystemExit(1)
            # Cria subclasse com yaml_file apontando para o arquivo customizado
            CustomConfig = type(
                "CustomConfig",
                (AppConfig,),
                {"model_config": SettingsConfigDict(
                    yaml_file=str(path),
                    yaml_file_encoding="utf-8",
                    env_nested_delimiter="__",
                    env_prefix="AIOPS_",
                    frozen=True,
                    extra="ignore",
                )},
            )
            return CustomConfig()
        return AppConfig()
    except ValidationError as exc:
        print("Error: Invalid configuration:", file=sys.stderr)
        for error in exc.errors():
            loc = " -> ".join(str(x) for x in error["loc"])
            print(f"  - {loc}: {error['msg']}", file=sys.stderr)
        raise SystemExit(1)
