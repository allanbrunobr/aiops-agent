"""Testes para Story 1.8 — Wire main.py to Blueprint Engine.

Valida que main.py usa o Blueprint Engine, preserva CLI flags e exit codes.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone


def test_create_incident_state_has_required_fields():
    """create_incident_state() deve produzir IncidentState valido."""
    from argparse import Namespace
    from main import create_incident_state

    args = Namespace(no_llm=True, no_rag=True, scenario=None, output_format="markdown", compact=False)
    state = create_incident_state(args, {"logs": [], "metrics": [], "traces": []})

    assert state.incident_id.startswith("inc-")
    assert state.no_llm is True
    assert state.no_rag is True
    assert state.alert is not None
    assert state.metadata["raw_incident_data"] is not None


def test_create_incident_state_with_scenario():
    """create_incident_state() deve aceitar scenario personalizado."""
    from argparse import Namespace
    from main import create_incident_state

    args = Namespace(no_llm=False, no_rag=False, scenario="memory-leak", output_format="json", compact=True)
    state = create_incident_state(args, {"logs": [1], "metrics": [2], "traces": [3]})

    assert state.metadata["scenario"] == "memory-leak"
    assert state.metadata["output_format"] == "json"
    assert state.metadata["compact"] is True
    assert state.no_llm is False


def test_main_v1_no_llm_no_rag(monkeypatch):
    """main_v1() com --no-llm --no-rag deve completar com EXIT_SUCCESS."""
    import sys
    from argparse import Namespace
    from main import main_v1, EXIT_SUCCESS

    args = Namespace(
        no_llm=True, no_rag=True, compact=True,
        config=None, scenario=None, output_format="markdown",
        output_dir="/tmp/aiops-test-output", rebuild_rag=False,
        data_source="simulated", tracker=None,
    )

    exit_code = main_v1(args)
    assert exit_code == EXIT_SUCCESS


def test_exit_codes_preserved():
    """Exit codes devem ser preservados de V0."""
    from main import EXIT_SUCCESS, EXIT_ERROR, EXIT_REGRESSION, EXIT_APPROVAL_NEEDED

    assert EXIT_SUCCESS == 0
    assert EXIT_ERROR == 1
    assert EXIT_REGRESSION == 2
    assert EXIT_APPROVAL_NEEDED == 3


def test_exit_code_labels_preserved():
    """Exit code labels devem ser preservados de V0."""
    from main import EXIT_CODE_LABELS, EXIT_SUCCESS, EXIT_ERROR, EXIT_REGRESSION, EXIT_APPROVAL_NEEDED

    assert EXIT_CODE_LABELS[EXIT_SUCCESS] == "resolved"
    assert EXIT_CODE_LABELS[EXIT_ERROR] == "error"
    assert EXIT_CODE_LABELS[EXIT_REGRESSION] == "regression"
    assert EXIT_CODE_LABELS[EXIT_APPROVAL_NEEDED] == "approval-needed"


def test_display_results_with_diagnosis():
    """display_results() deve exibir diagnosis sem erro."""
    from main import display_results
    from domain.alert import NormalizedAlert
    from domain.incident_state import IncidentState

    state = IncidentState(
        incident_id="test-display",
        correlation_id="test-display",
        received_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        alert=NormalizedAlert(
            source="test", source_id="d1", correlation_id="test-display",
            title="Test",
            description="Teste de pipeline node",
            severity="error",
            alert_type="TestAlert",
            timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
            detection_timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        diagnosis={
            "root_cause": "Test root cause",
            "evidence": ["ev1", "ev2"],
            "confidence": "high",
        },
        postmortem_md="# Test Postmortem",
    )

    # Nao deve levantar excecao
    display_results(state, compact=True)


def test_build_arg_parser_still_works():
    """build_arg_parser() deve continuar funcionando."""
    from main import build_arg_parser

    parser = build_arg_parser()
    args = parser.parse_args(["--no-llm", "--no-rag", "--compact"])
    assert args.no_llm is True
    assert args.no_rag is True
    assert args.compact is True
