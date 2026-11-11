"""A CLI decide aprovação humana e exit code com TODAS as camadas pré-execução.

Regressão de F03 (docs/analise-20261109.md): requires_human, o exit code e o
evento Slack olhavam só a camada pre_validator; o veredito do VersionValidator
(step_remediation_validation) era exibido no resumo e descartado.

Caso real, com as classes de verdade: `kubectl rollout restart
deployment/postgres:9.4` sai AUTO_APPROVED no PreExecutionValidator e
MALICIOUS no VersionValidator.
"""
from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import MagicMock, patch

import pytest

from domain.incident import MitigationAction

RISKY_ALLOWED = "kubectl rollout restart deployment/postgres:9.4"


def _layer(**attrs) -> MagicMock:
    m = MagicMock()
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


@pytest.mark.unit
class TestRequiresHumanCombinesLayers:
    @pytest.mark.parametrize(("pre", "rem", "expected"), [
        (None, None, False),
        (_layer(requires_human=False), _layer(is_safe=True), False),
        (_layer(requires_human=True), _layer(is_safe=True), True),
        (_layer(requires_human=False), _layer(is_safe=False), True),
        (None, _layer(is_safe=False), True),
        (_layer(requires_human=False), None, False),
    ])
    def test_combination(self, pre, rem, expected):
        from main import _requires_human
        assert _requires_human(pre, rem) is expected


@pytest.mark.unit
class TestRealLayersOnRiskyAllowedCommand:
    def test_version_validator_verdict_is_not_discarded(self):
        from main import (
            _record_safety_decision,
            _requires_human,
            step_remediation_validation,
            step_safety_validation,
        )

        diagnosis = {"root_cause": "x", "affected_services": ["postgres"], "confidence": "high"}
        action = MitigationAction(
            description="restart", command=RISKY_ALLOWED, risk_level="LOW",
            rollback_command="kubectl rollout undo deployment/postgres",
        )
        result = {"stages": {"diagnosis": diagnosis, "mitigation": {"action": action}}}

        # Mesma sequência do main(): as duas etapas e depois o veredito combinado.
        # Config que permite auto-aprovação: o cenário é "o pre-validator aprova
        # e o VersionValidator bloqueia". Com require_human_approval=true (o
        # padrão do YAML) o pre-validator nunca auto-aprova.
        from config.loader import AppConfig
        allow_auto = AppConfig(safety={"require_human_approval": False})
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            pre = step_safety_validation(result, config=allow_auto)
            rem = step_remediation_validation(result)
            _record_safety_decision(result, pre, rem)

        # O caso que o relatório descreve: uma camada aprova, a outra bloqueia.
        assert pre.requires_human is False
        assert rem.is_safe is False
        assert _requires_human(pre, rem) is True
        assert result["stages"]["safety_decision"]["requires_human"] is True
        assert "version_validator" in result["stages"]["safety_decision"]["blocked_by"]


def _main_with_layers(pre: MagicMock, rem: MagicMock):
    """Roda main() com as etapas pesadas substituídas; devolve (exit_code, slack_calls)."""
    from main import main

    mock_result = {
        "success": True,
        "stages": {
            "detection": {"is_incident": True, "severity": "HIGH", "signals": []},
            "diagnosis": {"root_cause": "test", "evidence": [], "affected_services": ["svc"],
                          "confidence": "high", "timeline": []},
            "mitigation": {"pattern_matched": "test", "action": None},
            "verification": {"result": "success", "health_delta": 10.0, "message": "ok"},
        },
        "timeline": [],
    }
    incident = MagicMock()
    incident.fix_time.__sub__ = MagicMock(
        return_value=MagicMock(total_seconds=MagicMock(return_value=600)))
    with (
        patch("sys.argv", ["main.py", "--no-llm", "--no-rag"]),
        patch("main.load_local_env"),
        patch("main.print_banner"),
        patch("main.step_generate_data", return_value=(incident, "test-scenario")),
        patch("main.step_sanitize", return_value=MagicMock(threat_detected=False, total_entries=100)),
        patch("main.step_pipeline", return_value=MagicMock()),
        patch("main.step_compression", return_value=(MagicMock(), "text", MagicMock(
            compression_ratio=0.5, critical_preserved=100, chunks_kept=5, chunks_removed=3))),
        patch("main.step_agents", return_value=mock_result),
        patch("main.step_safety_validation", return_value=pre),
        patch("main.step_remediation_validation", return_value=rem),
        patch("main.step_postmortem", return_value=(MagicMock(), None)),
        redirect_stdout(io.StringIO()),
        redirect_stderr(io.StringIO()),
    ):
        return main()


def _pre(requires_human: bool) -> MagicMock:
    m = MagicMock()
    m.requires_human = requires_human
    m.is_safe = True
    m.risk_level.value = "LOW"
    m.risk_score = 10
    m.approval_status.value = "auto_approved"
    m.blast_radius.scope = "single_pod"
    m.checks_passed, m.checks_failed, m.warnings = [], [], []
    return m


def _rem(is_safe: bool) -> MagicMock:
    m = MagicMock()
    m.is_safe = is_safe
    m.risk.name = "SAFE" if is_safe else "MALICIOUS"
    m.blocked_patterns = [] if is_safe else ["vulnerable version"]
    m.warnings, m.checks = [], []
    return m


@pytest.mark.unit
@pytest.mark.usefixtures("isolated_cli")
class TestMainExitCodeUsesAllLayers:
    def test_version_validator_block_yields_approval_needed(self):
        from main import EXIT_APPROVAL_NEEDED
        assert _main_with_layers(_pre(False), _rem(False)) == EXIT_APPROVAL_NEEDED

    def test_all_layers_ok_yields_success(self):
        from main import EXIT_SUCCESS
        assert _main_with_layers(_pre(False), _rem(True)) == EXIT_SUCCESS
