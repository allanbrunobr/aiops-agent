"""P4c — V1 imprime a tabela-resumo (print_summary) e usa o cache FAISS em disco."""

from __future__ import annotations

from argparse import Namespace
from io import StringIO
from unittest.mock import patch

import pytest
from rich.console import Console

pytestmark = pytest.mark.unit


def _args(tmp_path, **over) -> Namespace:
    base = dict(
        config=None,
        no_llm=True,
        no_rag=True,
        compact=True,
        scenario=None,
        output_format="markdown",
        output_dir=str(tmp_path / "out"),
        data_source="simulated",
        tracker=None,
        rebuild_rag=False,
    )
    base.update(over)
    return Namespace(**base)


def _run_capture(tmp_path, **over):
    from interfaces.cli import display, v1_runner

    buf = StringIO()
    cons = Console(file=buf, width=200, force_terminal=False)
    with (
        patch.object(display, "console", cons),
        patch.object(v1_runner, "console", cons),
        patch("app.slack_setup.setup_slack_notifier", return_value=None),
    ):
        code = v1_runner.run_v1(_args(tmp_path, **over))
    return code, buf.getvalue()


def test_v1_prints_summary_table_with_timeline(tmp_path):
    code, out = _run_capture(tmp_path)
    assert "Incident Response Summary" in out
    for stage in ("Scenario", "Detection", "Diagnosis", "Status"):
        assert stage in out
    assert "State Machine Timeline" in out
    assert code == 3  # exit code inalterado (no-llm: aguarda humano)


def test_summary_printed_after_outputs_saved(tmp_path):
    _, out = _run_capture(tmp_path)
    assert out.index("Incident Response Summary") > out.index("Postmortem")


def test_run_v1_passes_disk_cache_dir_and_rebuild(tmp_path):
    from blueprint.adapters import pipeline_nodes

    seen = {}
    real = pipeline_nodes._ensure_rag_index

    def spy(state, report):
        seen["dir"] = state.metadata.get("_rag_cache_dir")
        seen["rebuild"] = state.metadata.get("_rebuild_rag")
        return real(state, report)

    with patch.object(pipeline_nodes, "_ensure_rag_index", spy):
        _run_capture(tmp_path, no_rag=False, rebuild_rag=True)
    assert seen.get("dir") == ".aiops-cache/rag"
    assert seen.get("rebuild") is True
