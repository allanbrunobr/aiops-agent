"""O modelo do LLM vem de `llm.model_name` da config (A4/ARQ-08/F5-09).

P2 (docs/analise-20261109.md): main.py e o node diagnoser do V1 liam só
`os.environ["AIOPS_LLM_MODEL"]` com um literal de fallback; `AppConfig.llm`
não tinha leitor — mudar o modelo no YAML não tinha efeito.
Precedência: AIOPS_LLM_MODEL (legado, documentado) > llm.model_name > padrão.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from config.loader import AppConfig, resolve_llm_model


@pytest.mark.unit
class TestResolveLlmModel:
    def test_config_value_is_used(self, monkeypatch):
        monkeypatch.delenv("AIOPS_LLM_MODEL", raising=False)
        assert (
            resolve_llm_model(AppConfig(llm={"model_name": "claude-from-yaml"}))
            == "claude-from-yaml"
        )

    def test_legacy_env_variable_still_wins(self, monkeypatch):
        monkeypatch.setenv("AIOPS_LLM_MODEL", "claude-from-env")
        assert (
            resolve_llm_model(AppConfig(llm={"model_name": "claude-from-yaml"}))
            == "claude-from-env"
        )

    def test_no_config_uses_default(self, monkeypatch):
        monkeypatch.delenv("AIOPS_LLM_MODEL", raising=False)
        assert resolve_llm_model(None) == AppConfig().llm.model_name


@pytest.mark.unit
class TestEntrypointsUseTheConfig:
    def test_v1_diagnoser_node_uses_config_model(self, monkeypatch):
        from blueprint.adapters.pipeline_nodes import make_diagnoser_node

        monkeypatch.delenv("AIOPS_LLM_MODEL", raising=False)
        state = MagicMock()
        state.aggregated_report = {"_report_obj": MagicMock()}
        state.metadata = {}
        with (
            patch("agents.orchestrator.AgentOrchestrator") as orch,
            patch("adapters.llm.anthropic_client.create_anthropic_llm_client"),
            patch("dataclasses.replace", side_effect=lambda s, **kw: s),
        ):
            orch.return_value.run.return_value = MagicMock(mitigation={}, diagnosis={})
            make_diagnoser_node(AppConfig(llm={"model_name": "claude-from-yaml"}))(
                state
            )
        assert orch.call_args.kwargs["model"] == "claude-from-yaml"
