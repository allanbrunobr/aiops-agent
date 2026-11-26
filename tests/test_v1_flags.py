"""P3 (F5-4): flags do V0 honradas pelo run_v1.

--output-format, --tracker, --data-source databricks, --rebuild-rag e o
mkdir parents=True do save_postmortem_v1. Sem rede; tudo grava em tmp_path.
"""

from __future__ import annotations

import json
from argparse import Namespace
from unittest.mock import MagicMock, patch

import pytest

from interfaces.cli.exit_codes import EXIT_APPROVAL_NEEDED, EXIT_ERROR

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


def _files(tmp_path, suffix):
    return sorted((tmp_path / "out").glob(f"*{suffix}"))


class _FakeTracker:
    url = "https://tracker.example"

    def __init__(self):
        self.created = []
        self.postmortems = []

    def create_incident(self, title, severity, description):
        self.created.append((title, severity))
        return "AIOPS-777"

    def create_postmortem(self, key, timeline, root_cause, action_items):
        self.postmortems.append(key)
        return "c-1"


def test_save_postmortem_creates_nested_dirs(tmp_path):
    from interfaces.cli.display import save_postmortem_v1

    path = save_postmortem_v1("# x", output_dir=str(tmp_path / "a" / "b" / "c"))
    assert path is not None and path.read_text() == "# x"


@pytest.mark.parametrize(
    ("fmt", "md", "js"),
    [("markdown", 1, 0), ("json", 0, 1), ("both", 1, 1)],
)
def test_output_format_honored(tmp_path, fmt, md, js):
    from interfaces.cli.v1_runner import run_v1

    code = run_v1(_args(tmp_path, output_format=fmt))
    assert code == EXIT_APPROVAL_NEEDED
    assert len(_files(tmp_path, ".md")) == md
    assert len(_files(tmp_path, ".json")) == js
    if js:
        payload = json.loads(_files(tmp_path, ".json")[0].read_text())
        assert "diagnosis" in payload and "timeline" in payload


def test_tracker_created_attached_and_linked(tmp_path):
    from interfaces.cli.v1_runner import run_v1

    tracker = _FakeTracker()
    with patch(
        "interfaces.cli.v1_runner._resolve_tracker", return_value=tracker
    ) as resolve:
        code = run_v1(_args(tmp_path, tracker="jira", output_format="both"))
    resolve.assert_called_once_with("jira")
    assert code == EXIT_APPROVAL_NEEDED
    assert len(tracker.created) == 1
    assert tracker.postmortems == ["AIOPS-777"]
    md = _files(tmp_path, ".md")[0].read_text()
    assert "Incident Tracking" in md and "AIOPS-777" in md
    payload = json.loads(_files(tmp_path, ".json")[0].read_text())
    assert payload["tracking"]["issue_key"] == "AIOPS-777"


def test_no_tracker_means_no_tracking(tmp_path):
    from interfaces.cli.v1_runner import run_v1

    with patch("interfaces.cli.v1_runner.step_tracking") as step:
        run_v1(_args(tmp_path))
    step.assert_not_called()
    assert "Incident Tracking" not in _files(tmp_path, ".md")[0].read_text()


def test_databricks_missing_env_is_error(tmp_path):
    from interfaces.cli.v1_runner import run_v1

    with patch(
        "interfaces.cli.v1_runner._create_databricks_sources", return_value=None
    ):
        code = run_v1(_args(tmp_path, data_source="databricks"))
    assert code == EXIT_ERROR
    assert not _files(tmp_path, ".md")


def test_databricks_source_feeds_pipeline(tmp_path):
    from data.sample_incident import create_incident
    from interfaces.cli.v1_runner import run_v1

    incident = create_incident()
    srcs = (MagicMock(), MagicMock(), MagicMock())
    with (
        patch("interfaces.cli.v1_runner._create_databricks_sources", return_value=srcs),
        patch(
            "interfaces.cli.v1_runner.step_fetch_databricks_data",
            return_value=(incident, "databricks-live"),
        ) as fetch,
        patch("interfaces.cli.v1_runner.get_scenario") as scen,
    ):
        code = run_v1(_args(tmp_path, data_source="databricks"))
    fetch.assert_called_once()
    assert fetch.call_args.args[:3] == srcs
    scen.assert_not_called()
    assert code == EXIT_APPROVAL_NEEDED
    assert "databricks-live" in _files(tmp_path, ".md")[0].read_text()


def _capture_run(tmp_path, **over):
    """Roda run_v1 com engine/RAG falsos; devolve o estado que chegou à engine."""
    from interfaces.cli.v1_runner import run_v1

    seen = {}
    engine = MagicMock()

    def _run(pipeline, state):
        seen["state"] = state
        return state

    engine.run.side_effect = _run
    with (
        patch("app.builders.build_engine", return_value=engine),
        patch("app.builders.build_rag", return_value=(MagicMock(), MagicMock())),
    ):
        run_v1(_args(tmp_path, **over))
    return seen["state"]


def test_rebuild_rag_threaded_into_state(tmp_path):
    state = _capture_run(tmp_path, no_rag=False, rebuild_rag=True)
    assert state.metadata["_rebuild_rag"] is True
    assert state.metadata["_rag_cache_dir"]


def test_rebuild_rag_default_false(tmp_path):
    state = _capture_run(tmp_path, no_rag=False)
    assert state.metadata["_rebuild_rag"] is False


def test_rebuild_rag_with_no_rag_warns(tmp_path, capsys):
    state = _capture_run(tmp_path, no_rag=True, rebuild_rag=True)
    assert "rebuild-rag" in capsys.readouterr().out.lower()
    assert "_retriever" not in state.metadata


def _rag_state(rebuild, cache_dir):
    from datetime import datetime, timezone

    from domain.incident_state import IncidentState

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    indexer = MagicMock()
    indexer.index = None
    retriever = MagicMock()
    retriever.indexer = indexer
    state = IncidentState(
        incident_id="i",
        alert=None,
        correlation_id="i",
        received_at=now,
        started_at=now,
        no_rag=False,
        metadata={
            "_retriever": retriever,
            "_rebuild_rag": rebuild,
            "_rag_cache_dir": cache_dir,
        },
    )
    return state, indexer


def test_ensure_rag_index_uses_cache_unless_rebuild(tmp_path):
    from blueprint.adapters.pipeline_nodes import _ensure_rag_index

    report = MagicMock(log_templates=[], metric_anomalies=[], slow_traces=[])
    cache = str(tmp_path / "rag")

    state, indexer = _rag_state(False, cache)
    indexer.load_index.return_value = True
    _ensure_rag_index(state, report)
    indexer.load_index.assert_called_once()
    indexer.build_index.assert_not_called()

    state, indexer = _rag_state(True, cache)
    _ensure_rag_index(state, report)
    indexer.load_index.assert_not_called()
    indexer.build_index.assert_called_once()
    indexer.save_index.assert_called_once()
    assert indexer.save_index.call_args.args[0] == cache
