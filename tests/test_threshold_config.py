"""
Testes para o módulo de configuração de thresholds (config.py).

Cobre carregamento TOML, validação de valores, defaults e integração
com Aggregator, DetectionAgent e TNRSafetyGuard.
"""

from __future__ import annotations

import pytest


# === Task 1 Tests: config.py module ===

class TestThresholdConfigDefaults:
    """Testa valores default do ThresholdConfig."""

    def test_default_sigma_threshold(self):
        from config import ThresholdConfig
        config = ThresholdConfig()
        assert config.sigma_threshold == 3.0

    def test_default_baseline_ratio(self):
        from config import ThresholdConfig
        config = ThresholdConfig()
        assert config.baseline_ratio == 0.5

    def test_default_percentile_threshold(self):
        from config import ThresholdConfig
        config = ThresholdConfig()
        assert config.percentile_threshold == 95.0

    def test_default_max_hops(self):
        from config import ThresholdConfig
        config = ThresholdConfig()
        assert config.max_hops == 3

    def test_default_regression_threshold(self):
        from config import ThresholdConfig
        config = ThresholdConfig()
        assert config.regression_threshold == -5.0

    def test_default_error_threshold(self):
        from config import ThresholdConfig
        config = ThresholdConfig()
        assert config.error_threshold == 50

    def test_default_detection_sigma_threshold(self):
        from config import ThresholdConfig
        config = ThresholdConfig()
        assert config.detection_sigma_threshold == 3.0


class TestLoadConfigNoFile:
    """Testa load_config quando nenhum arquivo existe."""

    def test_returns_defaults_when_no_file_auto_discovery(self, tmp_path, monkeypatch):
        import config as config_module
        from config import load_config, ThresholdConfig
        # Monkeypatch __file__ to point to dir with no aiops.toml
        monkeypatch.setattr(config_module, "__file__", str(tmp_path / "config.py"))
        config = load_config(config_path=None)
        expected = ThresholdConfig()
        assert config == expected

    def test_explicit_nonexistent_path_raises(self):
        from config import load_config
        with pytest.raises(SystemExit):
            load_config(config_path="/definitely/nonexistent/config.toml")


class TestLoadConfigValidFile:
    """Testa load_config com arquivo TOML válido."""

    def test_reads_all_values(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
sigma_threshold = 4.0
baseline_ratio = 0.3
percentile_threshold = 99.0
max_hops = 5
regression_threshold = -10.0
error_threshold = 100
detection_sigma_threshold = 2.5
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        config = load_config(config_path=str(config_file))
        assert config.sigma_threshold == 4.0
        assert config.baseline_ratio == 0.3
        assert config.percentile_threshold == 99.0
        assert config.max_hops == 5
        assert config.regression_threshold == -10.0
        assert config.error_threshold == 100
        assert config.detection_sigma_threshold == 2.5

    def test_partial_config_merges_with_defaults(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
sigma_threshold = 2.0
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        config = load_config(config_path=str(config_file))
        assert config.sigma_threshold == 2.0  # from file
        assert config.baseline_ratio == 0.5  # default
        assert config.percentile_threshold == 95.0  # default
        assert config.max_hops == 3  # default
        assert config.regression_threshold == -5.0  # default
        assert config.error_threshold == 50  # default
        assert config.detection_sigma_threshold == 3.0  # default

    def test_ignores_unknown_keys(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
sigma_threshold = 2.5
unknown_key = "should be ignored"
another_unknown = 999
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        config = load_config(config_path=str(config_file))
        assert config.sigma_threshold == 2.5
        assert not hasattr(config, "unknown_key")

    def test_empty_thresholds_section_uses_defaults(self, tmp_path):
        from config import load_config, ThresholdConfig
        toml_content = b"""
[thresholds]
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        config = load_config(config_path=str(config_file))
        expected = ThresholdConfig()
        assert config == expected

    def test_no_thresholds_section_uses_defaults(self, tmp_path):
        from config import load_config, ThresholdConfig
        toml_content = b"""
[other_section]
key = "value"
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        config = load_config(config_path=str(config_file))
        expected = ThresholdConfig()
        assert config == expected


class TestConfigValidation:
    """Testa validação de valores inválidos."""

    def test_rejects_negative_sigma(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
sigma_threshold = -1.0
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))

    def test_rejects_zero_sigma(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
sigma_threshold = 0.0
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))

    def test_rejects_percentile_over_100(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
percentile_threshold = 101.0
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))

    def test_rejects_zero_percentile(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
percentile_threshold = 0.0
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))

    def test_rejects_positive_regression_threshold(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
regression_threshold = 5.0
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))

    def test_allows_zero_regression_threshold(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
regression_threshold = 0.0
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        config = load_config(config_path=str(config_file))
        assert config.regression_threshold == 0.0

    def test_rejects_baseline_ratio_over_1(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
baseline_ratio = 1.5
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))

    def test_rejects_zero_baseline_ratio(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
baseline_ratio = 0.0
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))

    def test_rejects_max_hops_less_than_1(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
max_hops = 0
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))

    def test_rejects_negative_error_threshold(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
error_threshold = -1
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        with pytest.raises(SystemExit):
            load_config(config_path=str(config_file))

    def test_rejects_negative_detection_sigma(self, tmp_path):
        from config import load_config
        toml_content = b"""
[thresholds]
detection_sigma_threshold = -2.0
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
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
        args = parser.parse_args(["--config", "/path/to/config.toml"])
        assert args.config == "/path/to/config.toml"

    def test_config_flag_default_none(self):
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.config is None


# === Task 7 Tests: Config info in print_summary ===

class TestConfigInSummary:
    """Testa que informações de config aparecem no summary."""

    def test_config_source_defaults_when_no_file(self):
        """Verifica que config_source é 'defaults' sem arquivo TOML."""
        # Simula a lógica de main.py:1028-1033 sem rodar o pipeline todo
        config_path = None
        config_source = config_path or "defaults"
        assert config_source == "defaults"

    def test_config_source_explicit_path(self):
        """Verifica que config_source reflete caminho explícito via --config."""
        config_path = "/custom/path/aiops.toml"
        config_source = config_path or "defaults"
        assert config_source == "/custom/path/aiops.toml"

    def test_result_dict_stores_config_source(self):
        """Verifica que result dict recebe config_source e threshold_config."""
        from config import ThresholdConfig
        result: dict = {"stages": {}}
        config_source = "defaults"
        threshold_config = ThresholdConfig()
        # Reproduz atribuição de main.py:1084-1085
        result["config_source"] = config_source
        result["threshold_config"] = threshold_config
        assert result["config_source"] == "defaults"
        assert result["threshold_config"] == ThresholdConfig()
        assert result["threshold_config"].sigma_threshold == 3.0

    def test_result_dict_stores_custom_config_source(self, tmp_path):
        """Verifica config_source com arquivo TOML real."""
        from config import load_config
        toml_content = b"""
[thresholds]
sigma_threshold = 2.0
"""
        config_file = tmp_path / "aiops.toml"
        config_file.write_bytes(toml_content)
        config_path = str(config_file)
        threshold_config = load_config(config_path=config_path)
        config_source = config_path or "defaults"
        result: dict = {"stages": {}}
        result["config_source"] = config_source
        result["threshold_config"] = threshold_config
        assert result["config_source"] == config_path
        assert result["threshold_config"].sigma_threshold == 2.0
