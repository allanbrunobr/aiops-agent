"""P2 (F5-4): postmortem rico no V1 — as 12 seções do renderer V0.

V1 --no-llm --no-rag de ponta a ponta (sem rede; nada grava em output/).
"""

from __future__ import annotations

import dataclasses
from argparse import Namespace

import pytest

from config.loader import AppConfig

pytestmark = pytest.mark.unit

SECTIONS = [
    "## Summary",
    "## Root Cause",
    "## Evidence",
    "## Timeline",
    "### State Machine Transitions",
    "## Mitigation Proposed",
    "## Pre-Execution Safety Check",
    "## Remediation CVE/Version Validation",
    "## TNR Verification",
    "## Action Items",
    "## Lessons Learned",
]


def _args(tmp_path) -> Namespace:
    return Namespace(
        no_llm=True,
        no_rag=True,
        compact=False,
        scenario=None,
        output_format="markdown",
        output_dir=str(tmp_path),
    )


@pytest.fixture(scope="module")
def final_state(tmp_path_factory):
    from app.builders import build_engine, build_pipeline
    from data.sample_incident import create_incident
    from interfaces.cli.v1_runner import create_incident_state

    incident = create_incident()
    data = {
        "logs": incident.logs,
        "metrics": incident.metrics,
        "traces": incident.traces,
    }
    config = AppConfig()
    state = create_incident_state(
        _args(tmp_path_factory.mktemp("pm")), data, incident=incident
    )
    return build_engine(config).run(build_pipeline(config), state)


def test_create_incident_state_keeps_incident(tmp_path) -> None:
    from data.sample_incident import create_incident
    from interfaces.cli.v1_runner import create_incident_state

    incident = create_incident()
    state = create_incident_state(_args(tmp_path), {}, incident=incident)
    assert state.metadata["_incident"] is incident


@pytest.mark.parametrize("section", SECTIONS)
def test_postmortem_has_section(final_state, section: str) -> None:
    assert section in (final_state.postmortem_md or "")


def test_postmortem_content_is_structured_not_template(final_state) -> None:
    md = final_state.postmortem_md or ""
    assert "Deployment or configuration change" in md
    assert "not verified" in md.lower() or "not_verified" in md.lower()


def test_postmortem_without_incident_does_not_crash(final_state) -> None:
    from blueprint.adapters.pipeline_nodes import make_postmortem_node

    meta = {k: v for k, v in final_state.metadata.items() if k != "_incident"}
    state = dataclasses.replace(final_state, metadata=meta, postmortem_md=None)
    out = make_postmortem_node(AppConfig())(state)
    assert "## Root Cause" in (out.postmortem_md or "")
