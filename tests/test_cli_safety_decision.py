"""A CLI decide aprovação humana e exit code com TODAS as camadas pré-execução.

Regressão de F03 (docs/analise-20261109.md): requires_human, o exit code e o
evento Slack olhavam só a camada pre_validator; o veredito do VersionValidator
(version validator) era exibido no resumo e descartado.

Caso real, com as classes de verdade: `kubectl rollout restart
deployment/postgres:9.4` sai AUTO_APPROVED no PreExecutionValidator e
MALICIOUS no VersionValidator.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

RISKY_ALLOWED = "kubectl rollout restart deployment/postgres:9.4"


def _layer(**attrs) -> MagicMock:
    m = MagicMock()
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


@pytest.mark.unit
class TestRequiresHumanCombinesLayers:
    @pytest.mark.parametrize(
        ("pre", "rem", "expected"),
        [
            (None, None, False),
            (_layer(requires_human=False), _layer(is_safe=True), False),
            (_layer(requires_human=True), _layer(is_safe=True), True),
            (_layer(requires_human=False), _layer(is_safe=False), True),
            (None, _layer(is_safe=False), True),
            (_layer(requires_human=False), None, False),
        ],
    )
    def test_combination(self, pre, rem, expected):
        from main import _requires_human

        assert _requires_human(pre, rem) is expected


@pytest.mark.unit
class TestRealLayersOnRiskyAllowedCommand:
    def test_version_validator_verdict_is_not_discarded(self):
        """V1: pre-validator aprova, VersionValidator bloqueia → grafo aborta (exit 3)."""
        import dataclasses
        from argparse import Namespace

        from blueprint.adapters.safety_nodes import (
            SafetyBlockedError,
            make_pre_validator_node,
            make_version_validator_node,
        )
        from config.loader import AppConfig
        from interfaces.cli.v1_runner import create_incident_state

        # Config que permite auto-aprovação: o cenário é "o pre-validator aprova
        # e o VersionValidator bloqueia". Com require_human_approval=true (o
        # padrão do YAML) o pre-validator nunca auto-aprova.
        cfg = AppConfig(safety={"require_human_approval": False})
        state = create_incident_state(
            Namespace(
                no_llm=True,
                no_rag=True,
                scenario=None,
                output_format="markdown",
                compact=True,
            ),
            {"logs": [], "metrics": [], "traces": []},
        )
        state = dataclasses.replace(
            state,
            diagnosis={
                "root_cause": "x",
                "affected_services": ["postgres"],
                "confidence": "high",
            },
            proposed_action={
                "description": "restart",
                "command": RISKY_ALLOWED,
                "risk_level": "LOW",
                "rollback": "kubectl rollout undo deployment/postgres",
            },
        )

        # O caso que o relatório descreve: uma camada aprova, a outra bloqueia.
        approved = make_pre_validator_node(cfg)(state)
        assert approved.safety_verdict == "APPROVED"
        with pytest.raises(SafetyBlockedError):
            make_version_validator_node(cfg)(state)
