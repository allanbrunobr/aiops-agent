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
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch, MagicMock

import pytest


@pytest.mark.unit
class TestExitCodeConstants:
    """Testes das constantes de exit code."""

    def test_exit_code_constants_defined(self) -> None:
        """Constantes de exit code devem ter valores corretos."""
        from main import EXIT_SUCCESS, EXIT_ERROR, EXIT_REGRESSION, EXIT_APPROVAL_NEEDED

        assert EXIT_SUCCESS == 0
        assert EXIT_ERROR == 1
        assert EXIT_REGRESSION == 2
        assert EXIT_APPROVAL_NEEDED == 3

    def test_exit_code_labels_defined(self) -> None:
        """EXIT_CODE_LABELS deve mapear todos os exit codes."""
        from main import EXIT_CODE_LABELS, EXIT_SUCCESS, EXIT_ERROR, EXIT_REGRESSION, EXIT_APPROVAL_NEEDED

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
        assert "1  Unrecoverable error" in help_text
        assert "2  TNR regression detected" in help_text
        assert "3  Safety blocked action" in help_text


@pytest.mark.unit
class TestMainExitCodes:
    """Testes de exit codes retornados por main()."""

    def test_main_returns_zero_on_success(self) -> None:
        """main() deve retornar 0 quando pipeline completa com sucesso."""
        from main import main, EXIT_SUCCESS

        test_args = ["main.py", "--no-llm", "--no-rag"]

        # Mock step functions para simular pipeline de sucesso.
        mock_result = {
            "success": True,
            "stages": {
                "detection": {"is_incident": True, "severity": "HIGH", "signals": []},
                "diagnosis": {"root_cause": "test", "evidence": [], "affected_services": ["svc"],
                              "confidence": "high", "timeline": []},
                "mitigation": {"pattern_matched": "test", "action": None},
                "verification": {"result": "success", "health_delta": 10.0, "message": "ok"},
                "safety_validation": {"is_safe": True, "requires_human": False},
                "remediation_validation": {"is_safe": True},
            },
            "timeline": [],
        }
        mock_orchestrator = MagicMock()
        mock_orchestrator.history = []

        mock_safety = MagicMock()
        mock_safety.requires_human = False
        mock_safety.risk_level.value = "LOW"
        mock_safety.risk_score = 10
        mock_safety.approval_status.value = "auto_approved"
        mock_safety.blast_radius.scope = "single_pod"
        mock_safety.blast_radius.affected_services = 1
        mock_safety.blast_radius.affected_pods_estimate = 1
        mock_safety.blast_radius.is_reversible = True
        mock_safety.blast_radius.is_data_destructive = False
        mock_safety.blast_radius.estimated_downtime_seconds = 0
        mock_safety.checks_passed = []
        mock_safety.checks_failed = []
        mock_safety.warnings = []
        mock_safety.recommendation = "OK"

        mock_remediation = MagicMock()
        mock_remediation.is_safe = True
        mock_remediation.risk.name = "SAFE"
        mock_remediation.blocked_patterns = []
        mock_remediation.warnings = []
        mock_remediation.checks = []

        mock_incident = MagicMock()
        mock_incident.deploy_time = MagicMock()
        mock_incident.fix_time = MagicMock()
        mock_incident.fix_time.__sub__ = MagicMock(return_value=MagicMock(total_seconds=MagicMock(return_value=600)))

        with (
            patch("sys.argv", test_args),
            patch("main.load_dotenv"),
            patch("main.print_banner"),
            patch("main.step_generate_data", return_value=(mock_incident, "test-scenario")),
            patch("main.step_sanitize", return_value=MagicMock(threat_detected=False, total_entries=100)),
            patch("main.step_pipeline", return_value=MagicMock()),
            patch("main.step_compression", return_value=(MagicMock(), "text", MagicMock(
                compression_ratio=0.5, critical_preserved=100, chunks_kept=5, chunks_removed=3))),
            patch("main.step_agents", return_value=(mock_result, mock_orchestrator)),
            patch("main.step_safety_validation", return_value=mock_safety),
            patch("main.step_remediation_validation", return_value=mock_remediation),
            patch("main.step_postmortem", return_value=(MagicMock(), None)),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = main()

        assert exit_code == EXIT_SUCCESS

    def test_main_returns_one_on_exception(self) -> None:
        """main() deve retornar 1 quando exceção não-tratada ocorre."""
        from main import main, EXIT_ERROR

        test_args = ["main.py", "--no-llm", "--no-rag"]

        with (
            patch("sys.argv", test_args),
            patch("main.load_dotenv"),
            patch("main.print_banner"),
            patch("main.step_generate_data", side_effect=RuntimeError("stage failure")),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = main()

        assert exit_code == EXIT_ERROR

    def test_main_returns_two_on_regression(self) -> None:
        """main() deve retornar 2 quando TNR detecta regressão."""
        from main import main, EXIT_REGRESSION

        test_args = ["main.py", "--no-llm", "--no-rag"]

        mock_result = {
            "success": False,
            "stages": {
                "detection": {"is_incident": True, "severity": "HIGH", "signals": []},
                "diagnosis": {"root_cause": "test", "evidence": [], "affected_services": ["svc"],
                              "confidence": "high", "timeline": []},
                "mitigation": {"pattern_matched": "test", "action": None},
                "verification": {"result": "regression", "health_delta": -15.0,
                                 "message": "REGRESSION: health dropped"},
                "safety_validation": {"is_safe": True, "requires_human": False},
                "remediation_validation": {"is_safe": True},
                "rollback": {"message": "Rollback executado"},
            },
            "timeline": [],
        }
        mock_orchestrator = MagicMock()
        mock_orchestrator.history = []

        mock_safety = MagicMock()
        mock_safety.requires_human = False
        mock_safety.risk_level.value = "LOW"
        mock_safety.risk_score = 10
        mock_safety.approval_status.value = "auto_approved"
        mock_safety.blast_radius.scope = "single_pod"
        mock_safety.blast_radius.affected_services = 1
        mock_safety.blast_radius.affected_pods_estimate = 1
        mock_safety.blast_radius.is_reversible = True
        mock_safety.blast_radius.is_data_destructive = False
        mock_safety.blast_radius.estimated_downtime_seconds = 0
        mock_safety.checks_passed = []
        mock_safety.checks_failed = []
        mock_safety.warnings = []
        mock_safety.recommendation = "OK"

        mock_remediation = MagicMock()
        mock_remediation.is_safe = True
        mock_remediation.risk.name = "SAFE"
        mock_remediation.blocked_patterns = []
        mock_remediation.warnings = []
        mock_remediation.checks = []

        mock_incident = MagicMock()
        mock_incident.deploy_time = MagicMock()
        mock_incident.fix_time = MagicMock()
        mock_incident.fix_time.__sub__ = MagicMock(return_value=MagicMock(total_seconds=MagicMock(return_value=600)))

        with (
            patch("sys.argv", test_args),
            patch("main.load_dotenv"),
            patch("main.print_banner"),
            patch("main.step_generate_data", return_value=(mock_incident, "test-scenario")),
            patch("main.step_sanitize", return_value=MagicMock(threat_detected=False, total_entries=100)),
            patch("main.step_pipeline", return_value=MagicMock()),
            patch("main.step_compression", return_value=(MagicMock(), "text", MagicMock(
                compression_ratio=0.5, critical_preserved=100, chunks_kept=5, chunks_removed=3))),
            patch("main.step_agents", return_value=(mock_result, mock_orchestrator)),
            patch("main.step_safety_validation", return_value=mock_safety),
            patch("main.step_remediation_validation", return_value=mock_remediation),
            patch("main.step_postmortem", return_value=(MagicMock(), None)),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = main()

        assert exit_code == EXIT_REGRESSION

    def test_main_returns_three_on_approval_needed(self) -> None:
        """main() deve retornar 3 quando safety requer aprovação humana."""
        from main import main, EXIT_APPROVAL_NEEDED

        test_args = ["main.py", "--no-llm", "--no-rag"]

        mock_result = {
            "success": True,
            "stages": {
                "detection": {"is_incident": True, "severity": "HIGH", "signals": []},
                "diagnosis": {"root_cause": "test", "evidence": [], "affected_services": ["svc"],
                              "confidence": "high", "timeline": []},
                "mitigation": {"pattern_matched": "test", "action": None},
                "verification": {"result": "success", "health_delta": 10.0, "message": "ok"},
                "safety_validation": {"is_safe": False, "requires_human": True},
                "remediation_validation": {"is_safe": True},
            },
            "timeline": [],
        }
        mock_orchestrator = MagicMock()
        mock_orchestrator.history = []

        mock_safety = MagicMock()
        mock_safety.requires_human = True
        mock_safety.risk_level.value = "HIGH"
        mock_safety.risk_score = 80
        mock_safety.approval_status.value = "requires_human"
        mock_safety.blast_radius.scope = "cluster"
        mock_safety.blast_radius.affected_services = 5
        mock_safety.blast_radius.affected_pods_estimate = 20
        mock_safety.blast_radius.is_reversible = True
        mock_safety.blast_radius.is_data_destructive = False
        mock_safety.blast_radius.estimated_downtime_seconds = 60
        mock_safety.checks_passed = []
        mock_safety.checks_failed = ["blast_radius_exceeded"]
        mock_safety.warnings = []
        mock_safety.recommendation = "Requires human approval"

        mock_remediation = MagicMock()
        mock_remediation.is_safe = True
        mock_remediation.risk.name = "SAFE"
        mock_remediation.blocked_patterns = []
        mock_remediation.warnings = []
        mock_remediation.checks = []

        mock_incident = MagicMock()
        mock_incident.deploy_time = MagicMock()
        mock_incident.fix_time = MagicMock()
        mock_incident.fix_time.__sub__ = MagicMock(return_value=MagicMock(total_seconds=MagicMock(return_value=600)))

        with (
            patch("sys.argv", test_args),
            patch("main.load_dotenv"),
            patch("main.print_banner"),
            patch("main.step_generate_data", return_value=(mock_incident, "test-scenario")),
            patch("main.step_sanitize", return_value=MagicMock(threat_detected=False, total_entries=100)),
            patch("main.step_pipeline", return_value=MagicMock()),
            patch("main.step_compression", return_value=(MagicMock(), "text", MagicMock(
                compression_ratio=0.5, critical_preserved=100, chunks_kept=5, chunks_removed=3))),
            patch("main.step_agents", return_value=(mock_result, mock_orchestrator)),
            patch("main.step_safety_validation", return_value=mock_safety),
            patch("main.step_remediation_validation", return_value=mock_remediation),
            patch("main.step_postmortem", return_value=(MagicMock(), None)),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = main()

        assert exit_code == EXIT_APPROVAL_NEEDED

    def test_regression_takes_precedence_over_approval(self) -> None:
        """Regressão (exit 2) deve ter prioridade sobre approval-needed (exit 3)."""
        from main import main, EXIT_REGRESSION

        test_args = ["main.py", "--no-llm", "--no-rag"]

        mock_result = {
            "success": False,
            "stages": {
                "detection": {"is_incident": True, "severity": "HIGH", "signals": []},
                "diagnosis": {"root_cause": "test", "evidence": [], "affected_services": ["svc"],
                              "confidence": "high", "timeline": []},
                "mitigation": {"pattern_matched": "test", "action": None},
                "verification": {"result": "regression", "health_delta": -15.0,
                                 "message": "REGRESSION detected"},
                "safety_validation": {"is_safe": False, "requires_human": True},
                "remediation_validation": {"is_safe": True},
                "rollback": {"message": "Rollback executado"},
            },
            "timeline": [],
        }
        mock_orchestrator = MagicMock()
        mock_orchestrator.history = []

        mock_safety = MagicMock()
        mock_safety.requires_human = True
        mock_safety.risk_level.value = "HIGH"
        mock_safety.risk_score = 80
        mock_safety.approval_status.value = "requires_human"
        mock_safety.blast_radius.scope = "cluster"
        mock_safety.blast_radius.affected_services = 5
        mock_safety.blast_radius.affected_pods_estimate = 20
        mock_safety.blast_radius.is_reversible = True
        mock_safety.blast_radius.is_data_destructive = False
        mock_safety.blast_radius.estimated_downtime_seconds = 60
        mock_safety.checks_passed = []
        mock_safety.checks_failed = []
        mock_safety.warnings = []
        mock_safety.recommendation = "Requires human approval"

        mock_remediation = MagicMock()
        mock_remediation.is_safe = True
        mock_remediation.risk.name = "SAFE"
        mock_remediation.blocked_patterns = []
        mock_remediation.warnings = []
        mock_remediation.checks = []

        mock_incident = MagicMock()
        mock_incident.deploy_time = MagicMock()
        mock_incident.fix_time = MagicMock()
        mock_incident.fix_time.__sub__ = MagicMock(return_value=MagicMock(total_seconds=MagicMock(return_value=600)))

        with (
            patch("sys.argv", test_args),
            patch("main.load_dotenv"),
            patch("main.print_banner"),
            patch("main.step_generate_data", return_value=(mock_incident, "test-scenario")),
            patch("main.step_sanitize", return_value=MagicMock(threat_detected=False, total_entries=100)),
            patch("main.step_pipeline", return_value=MagicMock()),
            patch("main.step_compression", return_value=(MagicMock(), "text", MagicMock(
                compression_ratio=0.5, critical_preserved=100, chunks_kept=5, chunks_removed=3))),
            patch("main.step_agents", return_value=(mock_result, mock_orchestrator)),
            patch("main.step_safety_validation", return_value=mock_safety),
            patch("main.step_remediation_validation", return_value=mock_remediation),
            patch("main.step_postmortem", return_value=(MagicMock(), None)),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = main()

        assert exit_code == EXIT_REGRESSION


@pytest.mark.integration
class TestExitCodeIntegration:
    """Testes de integração: exit codes em pipeline real."""

    def test_no_llm_no_rag_returns_valid_exit_code(self) -> None:
        """Pipeline real com --no-llm --no-rag deve retornar exit code válido (0-3).

        O cenário default (payment-pool-exhaustion) retorna exit code 3
        porque o PreExecutionValidator classifica a ação como MEDIUM risk
        e requires_human=True. Isso é comportamento correto.
        """
        from main import main, EXIT_APPROVAL_NEEDED

        test_args = ["main.py", "--no-llm", "--no-rag"]

        with (
            patch("sys.argv", test_args),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = main()

        # O cenário default sempre requer aprovação humana (safety MEDIUM risk).
        assert exit_code == EXIT_APPROVAL_NEEDED

    def test_exit_code_is_integer(self) -> None:
        """main() deve retornar um inteiro válido (0-3)."""
        from main import main

        test_args = ["main.py", "--no-llm", "--no-rag"]

        with (
            patch("sys.argv", test_args),
            redirect_stdout(io.StringIO()),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = main()

        assert isinstance(exit_code, int)
        assert 0 <= exit_code <= 3
