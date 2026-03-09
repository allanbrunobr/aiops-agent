"""Testes para config/loader.py — Story 1.1."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.loader import AppConfig, ThresholdsConfig, load_config


class TestAppConfigDefaults:
    """Testes de carregamento com valores padrao."""

    def test_loads_from_defaults_yaml(self):
        """AppConfig carrega do defaults.yaml com valores corretos."""
        config = AppConfig()
        assert config.thresholds.sigma_threshold == 3.0
        assert config.thresholds.baseline_ratio == 0.5
        assert config.thresholds.percentile_threshold == 95.0
        assert config.thresholds.max_hops == 3
        assert config.thresholds.regression_threshold == -5.0
        assert config.thresholds.error_threshold == 50
        assert config.thresholds.detection_sigma_threshold == 3.0

    def test_safety_defaults(self):
        """SafetyConfig carrega com valores padrao."""
        config = AppConfig()
        assert config.safety.blast_radius_limit == 5
        assert config.safety.require_human_approval is True

    def test_blueprint_defaults(self):
        """BlueprintConfig carrega como stub desabilitado."""
        config = AppConfig()
        assert config.blueprint.enabled is False

    def test_webhook_defaults(self):
        """WebhookConfig carrega com valores padrao."""
        config = AppConfig()
        assert config.webhook.enabled is False
        assert config.webhook.port == 8000

    def test_slack_defaults(self):
        """SlackConfig carrega com valores padrao."""
        config = AppConfig()
        assert config.slack.enabled is False


class TestEnvVarOverrides:
    """Testes de override por variaveis de ambiente."""

    def test_env_overrides_yaml(self, monkeypatch):
        """Variaveis de ambiente com __ delimiter devem sobrescrever YAML."""
        monkeypatch.setenv("AIOPS_THRESHOLDS__SIGMA_THRESHOLD", "2.5")
        config = AppConfig()
        assert config.thresholds.sigma_threshold == 2.5

    def test_env_overrides_safety(self, monkeypatch):
        """Variaveis de ambiente sobrescrevem safety config."""
        monkeypatch.setenv("AIOPS_SAFETY__BLAST_RADIUS_LIMIT", "3")
        config = AppConfig()
        assert config.safety.blast_radius_limit == 3

    def test_env_overrides_webhook_port(self, monkeypatch):
        """Variaveis de ambiente sobrescrevem webhook port."""
        monkeypatch.setenv("AIOPS_WEBHOOK__PORT", "9000")
        config = AppConfig()
        assert config.webhook.port == 9000


class TestValidation:
    """Testes de validacao de valores invalidos."""

    def test_invalid_sigma_threshold_raises(self):
        """sigma_threshold <= 0 deve gerar ValidationError."""
        with pytest.raises(ValidationError, match="Must be > 0"):
            ThresholdsConfig(sigma_threshold=-1.0)

    def test_invalid_regression_threshold_raises(self):
        """regression_threshold > 0 deve gerar ValidationError."""
        with pytest.raises(ValidationError, match="must be <= 0"):
            ThresholdsConfig(regression_threshold=1.0)

    def test_invalid_detection_sigma_raises(self):
        """detection_sigma_threshold <= 0 deve gerar ValidationError."""
        with pytest.raises(ValidationError, match="Must be > 0"):
            ThresholdsConfig(detection_sigma_threshold=0)


class TestImmutability:
    """Testes de imutabilidade (frozen model)."""

    def test_frozen_model_prevents_mutation(self):
        """Modelo frozen deve impedir alteracao de atributos."""
        config = AppConfig()
        with pytest.raises(ValidationError):
            config.thresholds = ThresholdsConfig(sigma_threshold=1.0)

    def test_frozen_nested_model(self):
        """Nested model frozen deve impedir alteracao de campos."""
        thresholds = ThresholdsConfig()
        with pytest.raises(ValidationError):
            thresholds.sigma_threshold = 1.0


class TestCustomConfigPath:
    """Testes de carregamento com caminho customizado."""

    def test_custom_yaml_path(self, tmp_path):
        """--config /path/to/custom.yaml deve carregar do arquivo especificado."""
        custom_yaml = tmp_path / "custom.yaml"
        custom_yaml.write_text(
            "thresholds:\n  sigma_threshold: 2.0\n  regression_threshold: -3.0\n"
        )
        config = load_config(config_path=str(custom_yaml))
        assert config.thresholds.sigma_threshold == 2.0
        assert config.thresholds.regression_threshold == -3.0

    def test_missing_config_file_exits(self):
        """Arquivo inexistente com --config deve causar SystemExit(1)."""
        with pytest.raises(SystemExit):
            load_config(config_path="/nonexistent/path.yaml")


class TestBackwardCompat:
    """Testes de compatibilidade com ThresholdConfig antigo."""

    def test_all_seven_fields_present(self):
        """ThresholdsConfig deve ter todos os 7 campos do antigo ThresholdConfig."""
        config = ThresholdsConfig()
        assert hasattr(config, "sigma_threshold")
        assert hasattr(config, "baseline_ratio")
        assert hasattr(config, "percentile_threshold")
        assert hasattr(config, "max_hops")
        assert hasattr(config, "regression_threshold")
        assert hasattr(config, "error_threshold")
        assert hasattr(config, "detection_sigma_threshold")

    def test_default_values_match_old_config(self):
        """Defaults do ThresholdsConfig devem ser identicos ao antigo ThresholdConfig."""
        config = ThresholdsConfig()
        assert config.sigma_threshold == 3.0
        assert config.baseline_ratio == 0.5
        assert config.percentile_threshold == 95.0
        assert config.max_hops == 3
        assert config.regression_threshold == -5.0
        assert config.error_threshold == 50
        assert config.detection_sigma_threshold == 3.0
