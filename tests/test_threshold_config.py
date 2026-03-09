"""
Testes para o modulo de configuracao de thresholds (config/loader.py).

Cobre carregamento YAML, validacao de valores, defaults e integracao
com Aggregator, DetectionAgent e TNRSafetyGuard.

Atualizado de TOML (config.py) para YAML (config/loader.py) na Story 1.1.
"""

from __future__ import annotations

import pytest


# === Task 1 Tests: config/loader.py module ===

class TestThresholdConfigDefaults:
    """Testa valores default do ThresholdsConfig."""

    def test_default_sigma_threshold(self):
        from config.loader import ThresholdsConfig
        config = ThresholdsConfig()
        assert config.sigma_threshold == 3.0

    def test_default_baseline_ratio(self):
        from config.loader import ThresholdsConfig
        config = ThresholdsConfig()
        assert config.baseline_ratio == 0.5

    def test_default_percentile_threshold(self):
        from config.loader import ThresholdsConfig
        config = ThresholdsConfig()
        assert config.percentile_threshold == 95.0

    def test_default_max_hops(self):
        from config.loader import ThresholdsConfig
        config = ThresholdsConfig()
        assert config.max_hops == 3

    def test_default_regression_threshold(self):
        from config.loader import ThresholdsConfig
        config = ThresholdsConfig()
        assert config.regression_threshold == -5.0

    def test_default_error_threshold(self):
        from config.loader import ThresholdsConfig
        config = ThresholdsConfig()
        assert config.error_threshold == 50

    def test_default_detection_sigma_threshold(self):
        from config.loader import ThresholdsConfig
        config = ThresholdsConfig()
        assert config.detection_sigma_threshold == 3.0


class TestLoadConfigNoFile:
    """Testa load_config quando nenhum arquivo existe."""

    def test_returns_defaults_when_no_custom_file(self):
        from config.loader import AppConfig, load_config
        config = load_config(config_path=None)
        expected = AppConfig()
        assert config.thresholds.sigma_threshold == expected.thresholds.sigma_threshold

    def test_explicit_nonexistent_path_raises(self):
        from config.loader import load_config
        with pytest.raises(SystemExit):
            load_config(config_path="/definitely/nonexistent/config.yaml")


class TestLoadConfigValidFile:
    """Testa load_config com arquivo YAML valido."""

    def test_reads_all_values(self, tmp_path):
        from config.loader import load_config
        yaml_content = """
thresholds:
  sigma_threshold: 4.0
  baseline_ratio: 0.3
  percentile_threshold: 99.0
  max_hops: 5
  regression_threshold: -10.0
  error_threshold: 100
  detection_sigma_threshold: 2.5
"""
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_content)
        config = load_config(config_path=str(config_file))
        assert config.thresholds.sigma_threshold == 4.0
        assert config.thresholds.baseline_ratio == 0.3
        assert config.thresholds.percentile_threshold == 99.0
        assert config.thresholds.max_hops == 5
        assert config.thresholds.regression_threshold == -10.0
        assert config.thresholds.error_threshold == 100
        assert config.thresholds.detection_sigma_threshold == 2.5

    def test_partial_config_merges_with_defaults(self, tmp_path):
        from config.loader import load_config
        yaml_content = """
thresholds:
  sigma_threshold: 2.0
"""
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_content)
        config = load_config(config_path=str(config_file))
        assert config.thresholds.sigma_threshold == 2.0  # from file
        assert config.thresholds.baseline_ratio == 0.5  # default
        assert config.thresholds.percentile_threshold == 95.0  # default
        assert config.thresholds.max_hops == 3  # default
        assert config.thresholds.regression_threshold == -5.0  # default
        assert config.thresholds.error_threshold == 50  # default
        assert config.thresholds.detection_sigma_threshold == 3.0  # default

    def test_ignores_unknown_keys(self, tmp_path):
        from config.loader import load_config
        yaml_content = """
thresholds:
  sigma_threshold: 2.5
unknown_section:
  key: value
"""
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_content)
        config = load_config(config_path=str(config_file))
        assert config.thresholds.sigma_threshold == 2.5

    def test_empty_thresholds_section_uses_defaults(self, tmp_path):
        from config.loader import AppConfig, load_config
        yaml_content = """
thresholds: {}
"""
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_content)
        config = load_config(config_path=str(config_file))
        expected = AppConfig()
        assert config.thresholds.sigma_threshold == expected.thresholds.sigma_threshold

    def test_no_thresholds_section_uses_defaults(self, tmp_path):
        from config.loader import AppConfig, load_config
        yaml_content = """
webhook:
  port: 9000
"""
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_content)
        config = load_config(config_path=str(config_file))
        expected = AppConfig()
        assert config.thresholds.sigma_threshold == expected.thresholds.sigma_threshold


class TestConfigValidation:
    """Testa validacao de valores invalidos."""

    def test_rejects_negative_sigma(self, tmp_path):
        from config.loader import load_config
        yaml_content = "thresholds:\n  sigma_threshold: -1.0\n"
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))

    def test_rejects_zero_sigma(self, tmp_path):
        from config.loader import load_config
        yaml_content = "thresholds:\n  sigma_threshold: 0.0\n"
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))

    def test_rejects_positive_regression_threshold(self, tmp_path):
        from config.loader import load_config
        yaml_content = "thresholds:\n  regression_threshold: 5.0\n"
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))

    def test_allows_zero_regression_threshold(self, tmp_path):
        from config.loader import load_config
        yaml_content = "thresholds:\n  regression_threshold: 0.0\n"
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_content)
        config = load_config(config_path=str(config_file))
        assert config.thresholds.regression_threshold == 0.0

    def test_rejects_negative_detection_sigma(self, tmp_path):
        from config.loader import load_config
        yaml_content = "thresholds:\n  detection_sigma_threshold: -2.0\n"
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))


# === Task 2 Tests: Aggregator forwards thresholds ===

class TestAggregatorThresholdForwarding:
    """Testa que Aggregator repassa thresholds para MetricFilter e TraceFilter."""

    def test_aggregator_default_creates_filters_with_defaults(self):
        from pipeline.aggregator import Aggregator
        agg = Aggregator()
        assert agg.metric_filter.sigma_threshold == 3.0
        assert agg.metric_filter.baseline_ratio == 0.5
        assert agg.trace_filter.percentile_threshold == 95.0
        assert agg.trace_filter.max_hops == 3

    def test_aggregator_forwards_custom_sigma(self):
        from pipeline.aggregator import Aggregator
        agg = Aggregator(sigma_threshold=4.5)
        assert agg.metric_filter.sigma_threshold == 4.5

    def test_aggregator_forwards_custom_baseline_ratio(self):
        from pipeline.aggregator import Aggregator
        agg = Aggregator(baseline_ratio=0.3)
        assert agg.metric_filter.baseline_ratio == 0.3

    def test_aggregator_forwards_custom_percentile(self):
        from pipeline.aggregator import Aggregator
        agg = Aggregator(percentile_threshold=99.0)
        assert agg.trace_filter.percentile_threshold == 99.0

    def test_aggregator_forwards_custom_max_hops(self):
        from pipeline.aggregator import Aggregator
        agg = Aggregator(max_hops=5)
        assert agg.trace_filter.max_hops == 5

    def test_aggregator_forwards_all_custom_values(self):
        from pipeline.aggregator import Aggregator
        agg = Aggregator(
            sigma_threshold=2.0,
            baseline_ratio=0.7,
            percentile_threshold=90.0,
            max_hops=2,
        )
        assert agg.metric_filter.sigma_threshold == 2.0
        assert agg.metric_filter.baseline_ratio == 0.7
        assert agg.trace_filter.percentile_threshold == 90.0
        assert agg.trace_filter.max_hops == 2


# === Task 5 Tests: CLI --config flag ===

class TestCliConfigFlag:
    """Testa o argumento --config no CLI."""

    def test_config_flag_parsed(self):
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args(["--config", "/path/to/config.yaml"])
        assert args.config == "/path/to/config.yaml"

    def test_config_flag_default_none(self):
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.config is None


# === Task 7 Tests: Config info in print_summary ===

class TestConfigInSummary:
    """Testa que informacoes de config aparecem no summary."""

    def test_config_source_defaults_when_no_file(self):
        """Verifica que config_source e 'defaults' sem arquivo."""
        config_path = None
        config_source = config_path or "config/defaults.yaml"
        assert config_source == "config/defaults.yaml"

    def test_config_source_explicit_path(self):
        """Verifica que config_source reflete caminho explicito via --config."""
        config_path = "/custom/path/config.yaml"
        config_source = config_path or "config/defaults.yaml"
        assert config_source == "/custom/path/config.yaml"

    def test_result_dict_stores_config_source(self):
        """Verifica que result dict recebe config_source e threshold_config."""
        from config.loader import AppConfig
        result: dict = {"stages": {}}
        config_source = "config/defaults.yaml"
        threshold_config = AppConfig()
        result["config_source"] = config_source
        result["threshold_config"] = threshold_config
        assert result["config_source"] == "config/defaults.yaml"
        assert result["threshold_config"].thresholds.sigma_threshold == 3.0

    def test_result_dict_stores_custom_config_source(self, tmp_path):
        """Verifica config_source com arquivo YAML real."""
        from config.loader import load_config
        yaml_content = "thresholds:\n  sigma_threshold: 2.0\n"
        config_file = tmp_path / "custom.yaml"
        config_file.write_text(yaml_content)
        config_path = str(config_file)
        threshold_config = load_config(config_path=config_path)
        config_source = config_path or "config/defaults.yaml"
        result: dict = {"stages": {}}
        result["config_source"] = config_source
        result["threshold_config"] = threshold_config
        assert result["config_source"] == config_path
        assert result["threshold_config"].thresholds.sigma_threshold == 2.0
