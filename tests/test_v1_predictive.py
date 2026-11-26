"""P4a (F5-4): node preditivo + Slack progressivo no V1.

stages.prediction populado via state_to_result; eventos Slack
diagnosis.completed e prediction.warning/alert além dos 3 já existentes.
Sem rede; tudo grava em tmp_path.
"""

from __future__ import annotations

import dataclasses
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from interfaces.cli.exit_codes import EXIT_APPROVAL_NEEDED

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


class _Recorder:
    def __init__(self):
        self.events = []

    async def on_event(self, evt):
        self.events.append(evt)


def _prediction(level):
    return SimpleNamespace(
        risk_score=72.0,
        signals=["metric_trend"],
        event_level=level,
        eta_minutes=12.0,
        matched_history=[],
    )


def _run(tmp_path, prediction):
    from interfaces.cli.v1_runner import run_v1

    rec = _Recorder()
    with (
        patch("app.slack_setup.setup_slack_notifier", return_value=rec),
        patch(
            "pipeline.predictive_stage.run_predictive_stage", return_value=prediction
        ),
    ):
        code = run_v1(_args(tmp_path))
    return code, [e.event_type for e in rec.events], rec.events


def _state_with_report():
    from data.sample_incident import create_incident
    from interfaces.cli.v1_runner import create_incident_state

    inc = create_incident()
    state = create_incident_state(
        Namespace(no_llm=True, no_rag=True, scenario=None),
        {"logs": inc.logs, "metrics": inc.metrics, "traces": inc.traces},
        incident=inc,
    )
    return dataclasses.replace(state, aggregated_report={"_report_obj": object()})


def test_predictive_node_registered_after_compressor():
    from app.builders import build_pipeline
    from config.loader import load_config

    ids = [n.id for n in build_pipeline(load_config(None)).nodes]
    assert ids.index("predictive") == ids.index("context_compressor") + 1


@pytest.mark.parametrize("pred", [_prediction("prediction.warning"), None])
def test_predictive_node_populates_state_and_stage(pred):
    from blueprint.adapters.pipeline_nodes import make_predictive_node
    from blueprint.adapters.state_adapter import state_to_result
    from config.loader import load_config

    with patch("pipeline.predictive_stage.run_predictive_stage", return_value=pred):
        out = make_predictive_node(load_config(None))(_state_with_report())
    assert out.metadata["_prediction"] is pred
    assert state_to_result(out)["stages"]["prediction"] is pred


def test_five_event_types_fired_on_warning(tmp_path):
    code, types, _ = _run(tmp_path, _prediction("prediction.warning"))
    assert code == EXIT_APPROVAL_NEEDED
    assert {
        "pipeline.started",
        "prediction.warning",
        "diagnosis.completed",
        "pipeline.completed",
    } <= set(types)
    assert types.index("prediction.warning") < types.index("diagnosis.completed")
    assert types.index("diagnosis.completed") < types.index("pipeline.completed")


def test_alert_level_event_and_payload(tmp_path):
    _, types, events = _run(tmp_path, _prediction("prediction.alert"))
    assert "prediction.alert" in types and "prediction.warning" not in types
    evt = next(e for e in events if e.event_type == "prediction.alert")
    assert evt.data["risk_score"] == 72.0 and evt.data["eta_minutes"] == 12.0
    assert evt.correlation_id


def test_no_prediction_event_when_below_threshold(tmp_path):
    _, types, _ = _run(tmp_path, _prediction(None))
    assert not [t for t in types if t.startswith("prediction.")]
    assert "diagnosis.completed" in types


def test_diagnosis_completed_payload(tmp_path):
    _, _, events = _run(tmp_path, None)
    evt = next(e for e in events if e.event_type == "diagnosis.completed")
    assert evt.data["root_cause"]
    assert {"proposed_fix", "risk_level", "requires_human", "confidence"} <= set(
        evt.data
    )


def test_predictive_failure_does_not_break_pipeline(tmp_path):
    from interfaces.cli.v1_runner import run_v1

    with patch(
        "pipeline.predictive_stage.run_predictive_stage",
        side_effect=RuntimeError("boom"),
    ):
        assert run_v1(_args(tmp_path)) == EXIT_APPROVAL_NEEDED
