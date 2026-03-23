"""
Testes para configuração MCP via YAML (Story 5.6).

Verifica que McpConfig e AppConfig carregam, validam e permitem
overrides via variáveis de ambiente.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError


@pytest.mark.unit
class TestMcpRateLimitsConfig:
    """Testes para McpRateLimitsConfig."""

    def test_default_values(self):
        """Verifica defaults da arquitetura."""
        from config.loader import McpRateLimitsConfig

        cfg = McpRateLimitsConfig()
        assert cfg.diagnose == 10
        assert cfg.search == 60
        assert cfg.validate == 30

    def test_custom_values(self):
        """Verifica que valores custom são aceitos."""
        from config.loader import McpRateLimitsConfig

        cfg = McpRateLimitsConfig(diagnose=20, search=100, validate=50)
        assert cfg.diagnose == 20
        assert cfg.search == 100
        assert cfg.validate == 50

    def test_frozen(self):
        """Verifica que modelo é imutável após criação."""
        from config.loader import McpRateLimitsConfig

        cfg = McpRateLimitsConfig()
        with pytest.raises((ValidationError, TypeError)):
            cfg.diagnose = 999  # type: ignore


@pytest.mark.unit
class TestMcpConfig:
    """Testes para McpConfig."""

    def test_defaults(self):
        """Verifica defaults do McpConfig."""
        from config.loader import McpConfig

        cfg = McpConfig()
        assert cfg.enabled is False
        assert cfg.transport == "sse"
        assert cfg.sse_port == 3001
        assert "diagnose_incident" in cfg.enabled_tools
        assert "validate_action" in cfg.enabled_tools
        assert "search_incidents" in cfg.enabled_tools
        assert cfg.auth_method == "header"

    def test_invalid_transport_raises(self):
        """Verifica que transporte inválido levanta ValidationError."""
        from config.loader import McpConfig

        with pytest.raises(ValidationError) as exc_info:
            McpConfig(transport="websocket")
        assert "transport must be 'stdio' or 'sse'" in str(exc_info.value)

    def test_valid_stdio_transport(self):
        """Verifica que stdio é aceito como transporte."""
        from config.loader import McpConfig

        cfg = McpConfig(transport="stdio")
        assert cfg.transport == "stdio"

    def test_invalid_tool_raises(self):
        """Verifica que ferramenta desconhecida levanta ValidationError."""
        from config.loader import McpConfig

        with pytest.raises(ValidationError) as exc_info:
            McpConfig(enabled_tools=["diagnose_incident", "invalid_tool"])
        assert "Unknown MCP tools" in str(exc_info.value)

    def test_subset_of_tools(self):
        """Verifica que subconjunto válido de ferramentas é aceito."""
        from config.loader import McpConfig

        cfg = McpConfig(enabled_tools=["search_incidents"])
        assert cfg.enabled_tools == ["search_incidents"]

    def test_frozen(self):
        """Verifica que modelo é imutável."""
        from config.loader import McpConfig

        cfg = McpConfig()
        with pytest.raises((ValidationError, TypeError)):
            cfg.enabled = True  # type: ignore

    def test_rate_limits_nested(self):
        """Verifica que rate_limits é aninhado corretamente."""
        from config.loader import McpConfig, McpRateLimitsConfig

        cfg = McpConfig(rate_limits=McpRateLimitsConfig(diagnose=5))
        assert cfg.rate_limits.diagnose == 5
        assert cfg.rate_limits.search == 60  # default mantido


@pytest.mark.unit
class TestAppConfigMcpSection:
    """Testes para AppConfig com seção mcp."""

    def test_appconfig_has_mcp_field(self):
        """Verifica que AppConfig inclui campo mcp com defaults corretos."""
        from config.loader import AppConfig

        cfg = AppConfig()
        assert hasattr(cfg, "mcp")
        assert cfg.mcp.enabled is False
        assert cfg.mcp.transport == "sse"

    def test_mcp_rate_limits_defaults(self):
        """Verifica defaults dos rate limits via AppConfig."""
        from config.loader import AppConfig

        cfg = AppConfig()
        assert cfg.mcp.rate_limits.diagnose == 10
        assert cfg.mcp.rate_limits.search == 60
        assert cfg.mcp.rate_limits.validate == 30

    def test_env_var_override_transport(self, monkeypatch):
        """Verifica que AIOPS__MCP__TRANSPORT sobrescreve YAML."""
        monkeypatch.setenv("AIOPS_MCP__TRANSPORT", "stdio")
        # Reimporta para evitar cache
        import importlib
        import config.loader as loader_mod

        importlib.reload(loader_mod)
        cfg = loader_mod.AppConfig()
        assert cfg.mcp.transport == "stdio"
        monkeypatch.delenv("AIOPS_MCP__TRANSPORT", raising=False)

    def test_env_var_override_rate_limit(self, monkeypatch):
        """Verifica que AIOPS__MCP__RATE_LIMITS__DIAGNOSE sobrescreve nested value."""
        monkeypatch.setenv("AIOPS_MCP__RATE_LIMITS__DIAGNOSE", "20")
        import importlib
        import config.loader as loader_mod

        importlib.reload(loader_mod)
        cfg = loader_mod.AppConfig()
        assert cfg.mcp.rate_limits.diagnose == 20
        monkeypatch.delenv("AIOPS_MCP__RATE_LIMITS__DIAGNOSE", raising=False)

    def test_custom_yaml_loads_correctly(self, tmp_path):
        """Verifica que YAML custom com overrides MCP carrega corretamente."""
        yaml_content = """
mcp:
  enabled: true
  transport: stdio
  sse_port: 4000
  rate_limits:
    diagnose: 5
    search: 30
    validate: 15
"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(yaml_content)

        from config.loader import load_config

        cfg = load_config(str(config_file))
        assert cfg.mcp.enabled is True
        assert cfg.mcp.transport == "stdio"
        assert cfg.mcp.sse_port == 4000
        assert cfg.mcp.rate_limits.diagnose == 5

    def test_mcp_enabled_false_is_default(self):
        """Verifica que mcp.enabled=false é o default seguro."""
        from config.loader import AppConfig

        cfg = AppConfig()
        assert cfg.mcp.enabled is False

    def test_disabled_tool_not_in_list(self, tmp_path):
        """Verifica que ferramenta removida da lista não aparece."""
        yaml_content = """
mcp:
  enabled: true
  enabled_tools:
    - search_incidents
"""
        config_file = tmp_path / "test_config.yaml"
        config_file.write_text(yaml_content)

        from config.loader import load_config

        cfg = load_config(str(config_file))
        assert "search_incidents" in cfg.mcp.enabled_tools
        assert "diagnose_incident" not in cfg.mcp.enabled_tools
