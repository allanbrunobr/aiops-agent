"""
Módulo de configuração de thresholds de análise.

Carrega configuração a partir de arquivo TOML (aiops.toml) usando
tomllib (stdlib Python 3.11+). Centraliza validação de valores e
fornece defaults para todos os thresholds do pipeline.

Este módulo é importado APENAS por main.py — os módulos de pipeline
(metric_filter, trace_filter, tnr, detector) recebem thresholds
via parâmetros de construtor, nunca importam config diretamente.
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path


# Arquivo de configuração padrão no diretório raiz do projeto
_DEFAULT_CONFIG_FILENAME = "aiops.toml"


@dataclass
class ThresholdConfig:
    """Configuração de thresholds para análise de anomalias.

    Todos os valores possuem defaults que mantêm o comportamento
    original do pipeline (backward-compatible).
    """

    # MetricFilter: desvios padrão acima da baseline para anomalia
    sigma_threshold: float = 3.0

    # MetricFilter: fração da série temporal usada como baseline
    baseline_ratio: float = 0.5

    # TraceFilter: percentil acima do qual traces são "lentos"
    percentile_threshold: float = 95.0

    # TraceFilter: profundidade máxima do call path
    max_hops: int = 3

    # TNRSafetyGuard: delta mínimo de health score aceitável
    # -5.0 por design do paper STRATUS (NeurIPS 2025, UIUC + IBM Research):
    # "até 5 pontos de queda no health score é tolerado para evitar
    # rollbacks falso-positivos causados por jitter de métricas"
    regression_threshold: float = -5.0

    # DetectionAgent: número mínimo de erros para acionar incidente
    error_threshold: int = 50

    # DetectionAgent: sigma threshold para classificação de severidade
    detection_sigma_threshold: float = 3.0


def load_config(config_path: str | None = None) -> ThresholdConfig:
    """Carrega configuração de thresholds a partir de arquivo TOML.

    Se config_path for fornecido, carrega daquele arquivo (erro se não existe).
    Se config_path for None, faz auto-discovery de 'aiops.toml' no diretório
    do script main.py. Se não encontrar, retorna todos os defaults.

    Args:
        config_path: Caminho explícito para arquivo TOML (None = auto-discovery).

    Returns:
        ThresholdConfig com valores do arquivo mesclados com defaults.

    Raises:
        SystemExit: Se caminho explícito não existe ou valores são inválidos.
    """
    if config_path is not None:
        # Caminho explícito via --config: DEVE existir
        path = Path(config_path)
        if not path.exists():
            print(
                f"Error: Config file not found: {config_path}",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return _load_and_validate(path)

    # Auto-discovery: procura aiops.toml no diretório do projeto
    project_root = Path(__file__).parent
    default_path = project_root / _DEFAULT_CONFIG_FILENAME
    if default_path.exists():
        return _load_and_validate(default_path)

    # Nenhum arquivo encontrado → retorna todos os defaults
    return ThresholdConfig()


def _load_and_validate(path: Path) -> ThresholdConfig:
    """Carrega TOML e valida valores.

    Args:
        path: Caminho para arquivo TOML existente.

    Returns:
        ThresholdConfig validado.

    Raises:
        SystemExit: Se arquivo é inválido ou valores fora dos limites.
    """
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        print(f"Error: Invalid TOML in {path}: {e}", file=sys.stderr)
        raise SystemExit(1)

    thresholds = data.get("thresholds", {})

    # Mescla com defaults: só pega chaves conhecidas do ThresholdConfig
    known_fields = {f.name for f in ThresholdConfig.__dataclass_fields__.values()}
    config_kwargs = {
        k: v for k, v in thresholds.items() if k in known_fields
    }
    config = ThresholdConfig(**config_kwargs)

    _validate_config(config, source=str(path))
    return config


def _validate_config(config: ThresholdConfig, source: str = "config") -> None:
    """Valida que todos os thresholds estão em intervalos aceitáveis.

    Args:
        config: ThresholdConfig a validar.
        source: Identificador do arquivo fonte para mensagens de erro.

    Raises:
        SystemExit: Se algum valor está fora do intervalo permitido.
    """
    errors: list[str] = []

    if config.sigma_threshold <= 0:
        errors.append(
            f"sigma_threshold must be > 0, got {config.sigma_threshold}"
        )

    if config.baseline_ratio <= 0 or config.baseline_ratio > 1.0:
        errors.append(
            f"baseline_ratio must be > 0 and <= 1.0, got {config.baseline_ratio}"
        )

    if config.percentile_threshold <= 0 or config.percentile_threshold > 100:
        errors.append(
            f"percentile_threshold must be > 0 and <= 100, got {config.percentile_threshold}"
        )

    if config.max_hops < 1:
        errors.append(
            f"max_hops must be >= 1, got {config.max_hops}"
        )

    if config.regression_threshold > 0:
        errors.append(
            f"regression_threshold must be <= 0 (non-positive), got {config.regression_threshold}"
        )

    if config.error_threshold < 0:
        errors.append(
            f"error_threshold must be >= 0, got {config.error_threshold}"
        )

    if config.detection_sigma_threshold <= 0:
        errors.append(
            f"detection_sigma_threshold must be > 0, got {config.detection_sigma_threshold}"
        )

    if errors:
        print(f"Error: Invalid threshold config in {source}:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        raise SystemExit(1)
