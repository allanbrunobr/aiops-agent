"""O modelo do LLM vem de `llm.model_name` da config (A4/ARQ-08/F5-09).

P2 (docs/analise-20261109.md): main.py e o node diagnoser do V1 liam só
`os.environ["AIOPS_LLM_MODEL"]` com um literal de fallback; `AppConfig.llm`
não tinha leitor — mudar o modelo no YAML não tinha efeito.
Precedência: AIOPS_LLM_MODEL (legado, documentado) > llm.model_name > padrão.
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import pytest

from config.loader import AppConfig, resolve_llm_model


@pytest.mark.unit
class TestResolveLlmModel:
    def test_config_value_is_used(self, monkeypatch):
        monkeypatch.delenv("AIOPS_LLM_MODEL", raising=False)
        assert resolve_llm_model(AppConfig(llm={"model_name": "claude-from-yaml"})) == "claude-from-yaml"

    def test_legacy_env_variable_still_wins(self, monkeypatch):
        monkeypatch.setenv("AIOPS_LLM_MODEL", "claude-from-env")
        assert resolve_llm_model(AppConfig(llm={"model_name": "claude-from-yaml"})) == "claude-from-env"

    def test_no_config_uses_default(self, monkeypatch):
        monkeypatch.delenv("AIOPS_LLM_MODEL", raising=False)
        assert resolve_llm_model(None) == AppConfig().llm.model_name


@pytest.mark.unit
class TestEntrypointsUseTheConfig:
    def test_main_passes_config_model_to_agents(self, tmp_path, monkeypatch):
        import main

        monkeypatch.delenv("AIOPS_LLM_MODEL", raising=False)
        cfg = tmp_path / "aiops.yaml"
        cfg.write_text("llm:\n  model_name: claude-from-yaml\n")
        agents = MagicMock(return_value={"success": True, "stages": {
            "detection": {"is_incident": True, "severity": "HIGH", "signals": []},
            "diagnosis": {"root_cause": "x", "evidence": [], "affected_services": ["svc"],
                          "confidence": "high", "timeline": []},
            "mitigation": {"pattern_matched": "x", "action": None},
            "verification": {"result": "not_verified", "health_delta": 0.0, "message": ""},
        }, "timeline": []})
        incident = MagicMock()
        incident.fix_time.__sub__ = MagicMock(
            return_value=MagicMock(total_seconds=MagicMock(return_value=1)))
        with (
            patch("sys.argv", ["main.py", "--no-rag", "--config", str(cfg),
                               "--output-dir", str(tmp_path)]),
            patch("main.load_dotenv"), patch("main.print_banner"),
            patch("main.step_generate_data", return_value=(incident, "s")),
            patch("main.step_sanitize", return_value=MagicMock(threat_detected=False, total_entries=1)),
            patch("main.step_pipeline", return_value=MagicMock()),
            patch("main.step_compression", return_value=(MagicMock(), "t", MagicMock(
                compression_ratio=0.5, critical_preserved=1, chunks_kept=1, chunks_removed=0))),
            patch("main.step_agents", agents),
            patch("main.step_predict"),
            patch("main.step_safety_validation", return_value=None),
            patch("main.step_remediation_validation", return_value=None),
            patch("main.step_postmortem", return_value=(MagicMock(), None)),
            patch("main.step_persist_incident", return_value=True),
            redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()),
        ):
            main.main()
        assert agents.call_args.kwargs["llm_model"] == "claude-from-yaml"

    def test_v1_diagnoser_node_uses_config_model(self, monkeypatch):
        from blueprint.adapters.pipeline_nodes import make_diagnoser_node

        monkeypatch.delenv("AIOPS_LLM_MODEL", raising=False)
        state = MagicMock()
        state.aggregated_report = {"_report_obj": MagicMock()}
        state.metadata = {}
        with patch("agents.orchestrator.AgentOrchestrator") as orch, \
             patch("adapters.llm.anthropic_client.create_anthropic_llm_client"), \
             patch("dataclasses.replace", side_effect=lambda s, **kw: s):
            orch.return_value.run.return_value = MagicMock(mitigation={}, diagnosis={})
            make_diagnoser_node(AppConfig(llm={"model_name": "claude-from-yaml"}))(state)
        assert orch.call_args.kwargs["model"] == "claude-from-yaml"
