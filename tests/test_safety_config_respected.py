"""A configuração de safety é respeitada pela CLI (V0) e pelo pipeline V1.

P2 — S7/F13 (docs/analise-20261109.md): `safety.require_human_approval` e
`safety.blast_radius_limit` existiam em config/defaults.yaml, mas a CLI fixava
auto_approve_low_risk=True e max_blast_radius=3; o node V1 usava o limite da
config mas também ignorava require_human_approval.
"""
from __future__ import annotations

import dataclasses
import io
from argparse import Namespace
from contextlib import redirect_stdout

import pytest

from config.loader import AppConfig
from domain.incident import MitigationAction

READ_ONLY = MitigationAction(description="inspect", command="kubectl get pods -n prod",
                             risk_level="LOW", rollback_command="n/a")


def _cfg(require: bool, limit: int) -> AppConfig:
    return AppConfig(safety={"require_human_approval": require, "blast_radius_limit": limit})


def _v0(config, services):
    from main import step_safety_validation

    result = {"stages": {
        "diagnosis": {"root_cause": "x", "affected_services": services, "confidence": "high"},
        "mitigation": {"action": READ_ONLY},
    }}
    with redirect_stdout(io.StringIO()):
        return step_safety_validation(result, config=config)


@pytest.mark.unit
class TestCliRespectsSafetyConfig:
    def test_require_human_approval_disables_auto_approve(self):
        r = _v0(_cfg(require=True, limit=5), ["svc"])
        assert r.requires_human is True
        assert r.approval_status.value != "auto_approved"

    def test_auto_approve_allowed_when_config_allows(self):
        r = _v0(_cfg(require=False, limit=5), ["svc"])
        assert r.approval_status.value == "auto_approved"

    def test_blast_radius_limit_comes_from_config(self):
        r = _v0(_cfg(require=False, limit=2), ["a", "b", "c"])
        assert r.requires_human is True
        assert any("Blast radius too large" in c for c in r.checks_failed)


@pytest.mark.unit
class TestV1PreValidatorNodeRespectsSafetyConfig:
    def _run(self, config):
        from blueprint.adapters.safety_nodes import make_pre_validator_node
        from main import create_incident_state

        state = create_incident_state(
            Namespace(no_llm=True, no_rag=True, scenario=None, output_format="markdown",
                      compact=True),
            {"logs": [], "metrics": [], "traces": []},
        )
        state = dataclasses.replace(
            state,
            diagnosis={"confidence": "high", "affected_services": ["svc"]},
            proposed_action={"description": "inspect", "command": "kubectl get pods -n prod",
                             "risk_level": "LOW", "rollback": "n/a"},
        )
        return make_pre_validator_node(config)(state)

    def test_require_human_approval_means_needs_review(self):
        assert self._run(_cfg(require=True, limit=5)).safety_verdict == "NEEDS_REVIEW"

    def test_auto_approve_when_config_allows(self):
        assert self._run(_cfg(require=False, limit=5)).safety_verdict == "APPROVED"
