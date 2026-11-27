"""Testes para exit codes do pipeline (Story 3.2).

Valida:
- Constantes de exit code definidas corretamente (AC #1-#4)
- Documentação de exit codes no --help (AC #5)
- main() retorna exit code 0 em cenário de sucesso (AC #1, #6)
- main() retorna exit code 1 em caso de exceção (AC #2)
- main() retorna exit code 2 em caso de regressão TNR (AC #3)
- main() retorna exit code 3 quando safety requer aprovação humana (AC #4)
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import pytest


@pytest.mark.unit
class TestExitCodeConstants:
    """Testes das constantes de exit code."""

    def test_exit_code_constants_defined(self) -> None:
        """Constantes de exit code devem ter valores corretos."""
        from main import EXIT_APPROVAL_NEEDED, EXIT_ERROR, EXIT_REGRESSION, EXIT_SUCCESS

        assert EXIT_SUCCESS == 0
        assert EXIT_ERROR == 1
        assert EXIT_REGRESSION == 2
        assert EXIT_APPROVAL_NEEDED == 3

    def test_exit_code_labels_defined(self) -> None:
        """EXIT_CODE_LABELS deve mapear todos os exit codes."""
        from main import (
            EXIT_APPROVAL_NEEDED,
            EXIT_CODE_LABELS,
            EXIT_ERROR,
            EXIT_REGRESSION,
            EXIT_SUCCESS,
        )

        assert EXIT_CODE_LABELS[EXIT_SUCCESS] == "resolved"
        assert EXIT_CODE_LABELS[EXIT_ERROR] == "error"
        assert EXIT_CODE_LABELS[EXIT_REGRESSION] == "regression"
        assert EXIT_CODE_LABELS[EXIT_APPROVAL_NEEDED] == "approval-needed"


@pytest.mark.unit
class TestHelpShowsExitCodes:
    """Testes da documentação de exit codes no --help."""

    def test_help_contains_exit_codes_section(self) -> None:
        """--help deve conter seção 'Exit codes:' com todos os 4 códigos."""
        from main import build_arg_parser

        parser = build_arg_parser()
        help_text = parser.format_help()

        assert "Exit codes:" in help_text
        assert "0  Pipeline resolved successfully" in help_text
        assert "1  Invalid arguments, or an unrecoverable error" in help_text
        assert "2  TNR regression detected" in help_text
        assert "3  Safety blocked action" in help_text


class _FakeEngine:
    """Engine V1 falsa: devolve o estado (com veredito) ou levanta PipelineAborted."""

    def __init__(
        self,
        *,
        verdict: str | None = None,
        abort: str | None = None,
        tnr: tuple[str, str] | None = None,
        action: bool = False,
    ):
        self._verdict = verdict
        self._abort = abort
        self._tnr = tnr  # (result, verification_mode)
        self._action = action

    def run(self, _pipeline, state):
        import dataclasses

        from blueprint.engine import PipelineAborted

        if self._abort is not None:
            raise PipelineAborted(self._abort)
        meta = dict(state.metadata)
        if self._tnr is not None:
            meta["_verification"] = {
                "result": self._tnr[0],
                "verification_mode": self._tnr[1],
            }
        return dataclasses.replace(
            state,
            safety_verdict=self._verdict,
            proposed_action={"command": "kubectl rollout restart deploy/x"}
            if self._action
            else None,
            metadata=meta,
        )


def _run_main_v1(engine: _FakeEngine, tmp_path, *extra: str) -> int:
    """Roda main() (V1 é o default, sem flag) com a engine falsa; saída em tmp_path."""
    from main import main

    test_args = [
        "main.py",
        *extra,
        "--no-llm",
        "--no-rag",
        "--output-dir",
        str(tmp_path),
    ]
    with (
        patch("sys.argv", test_args),
        patch("main.load_local_env"),
        patch("interfaces.cli.v1_runner.load_local_env"),
        patch("app.builders.build_engine", return_value=engine),
        redirect_stdout(io.StringIO()),
        redirect_stderr(io.StringIO()),
    ):
        return main()


@pytest.mark.unit
class TestMainExitCodes:
    """Testes de exit codes retornados por main() (pipeline V1)."""

    def test_main_returns_zero_on_success(self, tmp_path) -> None:
        """main() deve retornar 0 quando pipeline completa com sucesso."""
        from main import EXIT_SUCCESS

        assert _run_main_v1(_FakeEngine(verdict="APPROVED"), tmp_path) == EXIT_SUCCESS

    def test_main_returns_one_on_exception(self, tmp_path) -> None:
        """main() deve retornar 1 quando o pipeline aborta por erro não-tratado."""
        from main import EXIT_ERROR

        engine = _FakeEngine(abort="stage failure")
        assert _run_main_v1(engine, tmp_path) == EXIT_ERROR

    def test_main_returns_two_on_regression(self, tmp_path) -> None:
        """main() deve retornar 2 quando TNR detecta regressão."""
        from main import EXIT_REGRESSION

        engine = _FakeEngine(abort="TNR regression: health dropped")
        assert _run_main_v1(engine, tmp_path) == EXIT_REGRESSION

    def test_main_returns_three_on_approval_needed(self, tmp_path) -> None:
        """main() deve retornar 3 quando safety requer aprovação humana."""
        from main import EXIT_APPROVAL_NEEDED

        engine = _FakeEngine(verdict="NEEDS_REVIEW")
        assert _run_main_v1(engine, tmp_path) == EXIT_APPROVAL_NEEDED

    def test_deprecated_pipeline_v1_flag_is_an_accepted_noop(self, tmp_path) -> None:
        """--pipeline-v1 não quebra callers antigos: mesmo exit, mesmo engine."""
        from main import EXIT_APPROVAL_NEEDED, EXIT_SUCCESS

        ok = _FakeEngine(verdict="APPROVED")
        review = _FakeEngine(verdict="NEEDS_REVIEW")
        assert _run_main_v1(ok, tmp_path, "--pipeline-v1") == EXIT_SUCCESS
        assert _run_main_v1(review, tmp_path, "--pipeline-v1") == EXIT_APPROVAL_NEEDED

    def test_regression_takes_precedence_over_approval(self, tmp_path) -> None:
        """Regressão (exit 2) deve ter prioridade sobre approval-needed (exit 3)."""
        from main import EXIT_REGRESSION

        engine = _FakeEngine(abort="regression detected; safety blocked")
        assert _run_main_v1(engine, tmp_path) == EXIT_REGRESSION


@pytest.mark.integration
class TestExitCodeIntegration:
    """Testes de integração: exit codes em pipeline real."""

    def test_no_llm_no_rag_returns_valid_exit_code(self, tmp_path) -> None:
        """Pipeline real com --no-llm --no-rag deve retornar exit code válido (0-3).

        O cenário default (payment-pool-exhaustion) retorna exit code 3
        porque o PreExecutionValidator classifica a ação como MEDIUM risk
        e requires_human=True. Isso é comportamento correto.
        """
        from main import EXIT_APPROVAL_NEEDED, main

        test_args = [
            "main.py",
            "--no-llm",
            "--no-rag",
            "--output-dir",
            str(tmp_path),
        ]

        with (
            patch("sys.argv", test_args),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = main()

        # O cenário default sempre requer aprovação humana (safety MEDIUM risk).
        assert exit_code == EXIT_APPROVAL_NEEDED

    def test_exit_code_is_integer(self, tmp_path) -> None:
        """main() deve retornar um inteiro válido (0-3)."""
        from main import main

        test_args = [
            "main.py",
            "--no-llm",
            "--no-rag",
            "--output-dir",
            str(tmp_path),
        ]

        with (
            patch("sys.argv", test_args),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = main()

        assert isinstance(exit_code, int)
        assert 0 <= exit_code <= 3


# --- B5 (AIOPS-76): matriz TNRResult × (estado terminal, success, exit code) ---

# (tnr_result, mode, safety_verdict) com ação proposta pendente -> esperado
_MATRIX = [
    # TNR medido success -> resolvido
    ("success", "real", None, ("RESOLVED", True, 0)),
    # revisão humana pendente vence mesmo com TNR medido success
    ("success", "real", "NEEDS_REVIEW", ("AWAITING_APPROVAL", False, 3)),
    ("success", "measured", "APPROVED", ("RESOLVED", True, 0)),
    # success SIMULADO não é medição: precisa de read-only auto-aprovado
    ("success", "simulated", "APPROVED", ("RESOLVED", True, 0)),
    ("success", "simulated", "NEEDS_REVIEW", ("AWAITING_APPROVAL", False, 3)),
    ("success", "simulated", None, ("AWAITING_APPROVAL", False, 3)),
    # NOT_VERIFIED + ação pendente: nunca exit 0
    ("not_verified", "measured", "NEEDS_REVIEW", ("AWAITING_APPROVAL", False, 3)),
    ("not_verified", "measured", None, ("AWAITING_APPROVAL", False, 3)),
    ("not_verified", "measured", "APPROVED", ("RESOLVED", True, 0)),  # read-only
    # regressão / rollback / erro -> exit 2, com precedência sobre aprovação
    ("regression", "real", "NEEDS_REVIEW", ("FAILED", False, 2)),
    ("rollback_complete", "real", "NEEDS_REVIEW", ("FAILED", False, 2)),
    ("rollback_complete", "real", "APPROVED", ("FAILED", False, 2)),
    ("error", "simulated", "NEEDS_REVIEW", ("FAILED", False, 2)),
    ("error", "simulated", "APPROVED", ("FAILED", False, 2)),
]


@pytest.mark.unit
class TestTnrExitMatrix:
    def test_matrix_covers_every_tnr_result(self) -> None:
        from safety.tnr import TNRResult

        covered = {row[0] for row in _MATRIX}
        assert covered == {r.value for r in TNRResult}

    @pytest.mark.parametrize("tnr,mode,verdict,expected", _MATRIX)
    def test_classify_terminal(self, tnr, mode, verdict, expected) -> None:
        from domain.terminal_state import classify_terminal

        out = classify_terminal(
            tnr_result=tnr,
            verification_mode=mode,
            has_proposed_action=True,
            auto_approved=verdict == "APPROVED",
            needs_review=verdict == "NEEDS_REVIEW",
        )
        assert (out.state, out.success, out.exit_code) == expected

    @pytest.mark.parametrize("tnr,mode,verdict,expected", _MATRIX)
    def test_cli_exit_code_and_result(
        self, tnr, mode, verdict, expected, tmp_path
    ) -> None:
        from blueprint.adapters.state_adapter import terminal_outcome

        engine = _FakeEngine(verdict=verdict, tnr=(tnr, mode), action=True)
        assert _run_main_v1(engine, tmp_path) == expected[2]
        state = engine.run(None, _minimal_state())
        out = terminal_outcome(state)
        assert (out.state, out.success, out.exit_code) == expected

    def test_not_verified_pending_action_never_exit_zero_or_resolved(
        self, tmp_path
    ) -> None:
        engine = _FakeEngine(
            verdict=None, tnr=("not_verified", "measured"), action=True
        )
        assert _run_main_v1(engine, tmp_path) == 3

    def test_no_pending_action_is_resolved(self, tmp_path) -> None:
        assert _run_main_v1(_FakeEngine(verdict=None), tmp_path) == 0

    def test_state_to_result_success_false_when_awaiting(self) -> None:
        from blueprint.adapters.state_adapter import state_to_result

        engine = _FakeEngine(
            verdict="NEEDS_REVIEW", tnr=("not_verified", "measured"), action=True
        )
        state = engine.run(None, _minimal_state())
        import dataclasses

        state = dataclasses.replace(state, diagnosis={"root_cause": "x"})
        res = state_to_result(state)
        assert res["success"] is False
        assert res["terminal_state"] == "AWAITING_APPROVAL"


def _minimal_state():
    from datetime import datetime, timezone

    from domain.alert import NormalizedAlert
    from domain.incident_state import IncidentState

    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    alert = NormalizedAlert(
        source="test",
        source_id="s1",
        correlation_id="b5",
        title="t",
        description="d",
        severity="error",
        alert_type="x",
        timestamp=now,
        detection_timestamp=now,
    )
    return IncidentState(
        incident_id="b5",
        correlation_id="b5",
        alert=alert,
        received_at=now,
        started_at=now,
    )
