from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict


class ThresholdsConfig(BaseModel):
    """Configuracao de limiares para deteccao de anomalias."""

    model_config = ConfigDict(frozen=True)

    sigma_threshold: float = 3.0
    p95_threshold_ms: float = 500.0
    min_error_rate: float = 0.05
    min_anomaly_score: float = 0.6
    correlation_window_seconds: int = 300
    min_correlated_signals: int = 2
    regression_threshold: float = -0.1


class SafetyConfig(BaseModel):
    """Configuracao das camadas de seguranca."""

    model_config = ConfigDict(frozen=True)

    blast_radius_limit: int = 5
    enable_command_blocklist: bool = True


class WebhookConfig(BaseModel):
    """Configuracao do subsistema de webhook."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    port: int = 8000
    max_payload_bytes: int = 1_048_576       # 1MB
    dedup_window_seconds: int = 300          # 5 minutos
    max_concurrent_pipelines: int = 3        # NFR3
    max_queued_alerts: int = 10              # Limite da fila


class SlackConfig(BaseModel):
    """Configuracao da integracao com Slack."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    channel: str = ""


class AppConfig(BaseModel):
    """Configuracao raiz da aplicacao AIOps Agent."""

    model_config = ConfigDict(frozen=True)

    thresholds: ThresholdsConfig = ThresholdsConfig()
    safety: SafetyConfig = SafetyConfig()
    webhook: WebhookConfig = WebhookConfig()
    slack: SlackConfig = SlackConfig()


def _load_yaml(path: Path) -> dict[str, Any]:
    """Carrega arquivo YAML e retorna dicionario. Retorna {} se nao encontrado."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return {}

    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Aplica sobrescrita de variaveis de ambiente com prefixo AIOPS__.

    Converte AIOPS__WEBHOOK__PORT=9000 em data["webhook"]["port"] = 9000.
    """
    prefix = "AIOPS__"
    for key, value in os.environ.items():
        if not key.startswith(prefix):
            continue
        parts = key[len(prefix):].lower().split("__")
        target = data
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        # Tenta converter para tipo numerico/booleano automaticamente
        target[parts[-1]] = _coerce(value)
    return data


def _coerce(value: str) -> Any:
    """Tenta converter string para int, float ou bool."""
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def load_config(yaml_path: Path | None = None) -> AppConfig:
    """Carrega configuracao do arquivo YAML com sobrescritas de env vars.

    Args:
        yaml_path: Caminho para arquivo YAML. Usa config/defaults.yaml se None.

    Returns:
        AppConfig validado e imutavel.
    """
    if yaml_path is None:
        yaml_path = Path(__file__).parent / "defaults.yaml"

    data = _load_yaml(yaml_path)
    data = _apply_env_overrides(data)
    return AppConfig(**data)
