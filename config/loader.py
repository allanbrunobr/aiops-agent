from __future__ import annotations

from pathlib import Path
from typing import Any, Tuple, Type

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class SlackConfig(BaseModel):
    """Configuracao do Slack Bot API — canal, retry e fallback."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    channel: str = ""                       # Slack channel ID (e.g., "C1234567890")
    max_retries: int = 3                    # Tentativas de retry antes do fallback
    initial_backoff_s: float = 1.0          # Backoff inicial em segundos
    backoff_multiplier: float = 2.0         # Multiplicador exponencial do backoff
    fallback_dir: str = "output"            # Diretorio para fallback local
    dry_run: bool = False                   # Se true, loga mas nao envia para Slack

    @field_validator("max_retries")
    @classmethod
    def max_retries_non_negative(cls, v: int) -> int:
        """Valida que max_retries nao e negativo."""
        if v < 0:
            raise ValueError(f"max_retries must be >= 0, got {v}")
        return v

    @field_validator("initial_backoff_s")
    @classmethod
    def backoff_positive(cls, v: float) -> float:
        """Valida que backoff inicial e positivo."""
        if v <= 0:
            raise ValueError(f"initial_backoff_s must be > 0, got {v}")
        return v

    @model_validator(mode="after")
    def channel_required_when_enabled(self) -> SlackConfig:
        """Valida que channel nao esta vazio quando enabled=true."""
        if self.enabled and not self.channel.strip():
            raise ValueError("slack.channel is required when slack.enabled is true")
        return self


class NotificationsConfig(BaseModel):
    """Configuracao de notificacoes — agrupa canais de notificacao."""

    model_config = ConfigDict(frozen=True)

    slack: SlackConfig = Field(default_factory=SlackConfig)


class ObservabilityConfig(BaseModel):
    """Configuracao de observabilidade — metricas e tracking."""

    model_config = ConfigDict(frozen=True)

    metric_retention_hours: int = 24            # Retencao de metricas em horas
    accuracy_tracking_enabled: bool = True      # Habilita tracking de acuracia

    @field_validator("metric_retention_hours")
    @classmethod
    def retention_at_least_one(cls, v: int) -> int:
        """Valida que retencao e pelo menos 1 hora."""
        if v < 1:
            raise ValueError(f"metric_retention_hours must be >= 1, got {v}")
        return v


class YamlConfigSettingsSource(PydanticBaseSettingsSource):
    """Fonte de configuracao via arquivo YAML."""

    def __init__(self, settings_cls: Type[BaseSettings], yaml_file: str | Path | None = None) -> None:
        super().__init__(settings_cls)
        self._yaml_file = Path(yaml_file) if yaml_file else None

    def get_field_value(self, field: Any, field_name: str) -> Tuple[Any, str, bool]:
        """Retorna valor do campo do YAML."""
        data = self._load_yaml()
        value = data.get(field_name)
        return value, field_name, False

    def _load_yaml(self) -> dict[str, Any]:
        """Carrega arquivo YAML se existir."""
        if self._yaml_file and self._yaml_file.exists():
            with open(self._yaml_file, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def __call__(self) -> dict[str, Any]:
        """Retorna todos os valores do YAML."""
        return self._load_yaml()


class AppConfig(BaseSettings):
    """Configuracao raiz do AIOps Agent."""

    model_config = SettingsConfigDict(
        env_nested_delimiter="__",
        env_prefix="AIOPS_",
        frozen=True,
        extra="ignore",
    )

    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> Tuple[PydanticBaseSettingsSource, ...]:
        """Define ordem de precedencia: init > env > yaml > defaults."""
        yaml_path = Path("config/defaults.yaml")
        yaml_settings = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path)
        return init_settings, env_settings, yaml_settings


def load_config(yaml_file: str | Path | None = None) -> AppConfig:
    """Carrega configuracao do AIOps Agent.

    Ordem de precedencia: env vars > YAML > defaults.
    """
    if yaml_file is not None:
        # Permite override do caminho YAML para testes
        yaml_path = Path(yaml_file)

        class _AppConfigWithYaml(AppConfig):
            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls: Type[BaseSettings],
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ) -> Tuple[PydanticBaseSettingsSource, ...]:
                yaml_settings = YamlConfigSettingsSource(settings_cls, yaml_file=yaml_path)
                return init_settings, env_settings, yaml_settings

        return _AppConfigWithYaml()

    return AppConfig()
