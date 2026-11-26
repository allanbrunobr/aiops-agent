"""
Testes unitários para seleção de cenário via CLI (--scenario flag).

Valida parsing de argumentos, integração com step_generate_data(),
e compatibilidade com demais flags (--no-llm, --no-rag).
"""

from __future__ import annotations

import io
from contextlib import redirect_stderr

import pytest

# Garante registro de todos os cenários no registry antes dos testes.
import data.sample_incident  # noqa: F401


@pytest.mark.unit
class TestArgparseScenarioFlag:
    """Testes do argparse para o flag --scenario (AC: #2, #3, #4, #5)."""

    def test_default_scenario_no_flag(self) -> None:
        """Sem --scenario, args.scenario deve ser None (backward-compatible)."""
        from main import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.scenario is None

    def test_scenario_flag_valid(self) -> None:
        """--scenario memory-leak deve ser parseado corretamente."""
        from main import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(["--scenario", "memory-leak"])
        assert args.scenario == "memory-leak"

    def test_scenario_flag_payment_pool(self) -> None:
        """--scenario payment-pool-exhaustion deve ser parseado corretamente."""
        from main import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(["--scenario", "payment-pool-exhaustion"])
        assert args.scenario == "payment-pool-exhaustion"

    def test_scenario_flag_invalid_exits(self) -> None:
        """--scenario nonexistent deve causar SystemExit (argparse auto-valida)."""
        from main import build_arg_parser

        parser = build_arg_parser()
        with pytest.raises(SystemExit) as exc_info:
            # Suprime stderr do argparse (mensagem de erro + usage).
            with redirect_stderr(io.StringIO()):
                parser.parse_args(["--scenario", "nonexistent"])
        assert exc_info.value.code == 1

    def test_scenario_with_no_llm_flag(self) -> None:
        """--scenario memory-leak --no-llm deve funcionar em conjunto."""
        from main import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(["--scenario", "memory-leak", "--no-llm"])
        assert args.scenario == "memory-leak"
        assert args.no_llm is True

    def test_scenario_with_no_rag_flag(self) -> None:
        """--scenario memory-leak --no-rag deve funcionar em conjunto."""
        from main import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(["--scenario", "memory-leak", "--no-rag"])
        assert args.scenario == "memory-leak"
        assert args.no_rag is True

    def test_scenario_with_all_flags(self) -> None:
        """--scenario com --no-llm e --no-rag simultaneamente deve funcionar."""
        from main import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(
            ["--scenario", "payment-pool-exhaustion", "--no-llm", "--no-rag"]
        )
        assert args.scenario == "payment-pool-exhaustion"
        assert args.no_llm is True
        assert args.no_rag is True


@pytest.mark.unit
class TestScenarioSelection:
    """Seleção de cenário via registry (o que ``run_v1`` usa) (AC: #1, #2)."""

    def test_default_scenario_is_payment_pool_exhaustion(self) -> None:
        from data.registry import get_scenario
        from data.sample_incident import create_incident

        incident = create_incident()
        assert "pool" in incident.actual_root_cause.lower()
        explicit = get_scenario("payment-pool-exhaustion")(seed=42)
        assert len(incident.logs) > 0
        assert len(incident.metrics) > 0
        assert len(incident.traces) > 0
        assert "pool" in explicit.actual_root_cause.lower()

    def test_memory_leak_scenario(self) -> None:
        from data.registry import get_scenario

        incident = get_scenario("memory-leak")(seed=42)
        assert "memory" in incident.actual_root_cause.lower()
        assert len(incident.logs) > 0
        assert len(incident.metrics) > 0
        assert len(incident.traces) > 0

    def test_scenario_returns_simulated_incident(self) -> None:
        from data.models import SimulatedIncident
        from data.registry import get_scenario

        assert isinstance(get_scenario("memory-leak")(seed=42), SimulatedIncident)
