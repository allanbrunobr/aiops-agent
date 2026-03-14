"""Testes para --output-dir CLI flag (Story 3.1).

Valida:
- Parsing do argumento --output-dir (AC #3, #4)
- Compatibilidade com demais flags (AC #6)
- Postmortem salvo em diretório customizado (AC #1, #2)
- Criação de diretórios aninhados (AC #2)
- Default backward-compatible (AC #3)
"""
from __future__ import annotations

import io
from contextlib import redirect_stdout, redirect_stderr

import pytest


# ──────────────────────────────────────────────────────────────────────
# Task 3: Unit tests for --output-dir CLI flag (AC #3, #4, #6)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestArgparseOutputDirFlag:
    """Testes do argparse para o flag --output-dir."""

    def test_output_dir_flag_accepted(self) -> None:
        """--output-dir /tmp/test deve ser parseado corretamente."""
        from main import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(["--output-dir", "/tmp/test"])
        assert args.output_dir == "/tmp/test"

    def test_output_dir_default_is_output(self) -> None:
        """Sem --output-dir, args.output_dir deve ser 'output' (backward-compatible)."""
        from main import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.output_dir == "output"

    def test_output_dir_combined_with_all_flags(self) -> None:
        """--output-dir com --scenario, --no-llm e --no-rag simultaneamente."""
        from main import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args([
            "--output-dir", "/tmp/x",
            "--scenario", "memory-leak",
            "--no-llm",
            "--no-rag",
        ])
        assert args.output_dir == "/tmp/x"
        assert args.scenario == "memory-leak"
        assert args.no_llm is True
        assert args.no_rag is True

    def test_help_contains_output_dir(self) -> None:
        """--help output deve conter referência a --output-dir."""
        from main import build_arg_parser

        parser = build_arg_parser()
        help_text = parser.format_help()
        assert "--output-dir" in help_text


# ──────────────────────────────────────────────────────────────────────
# Task 4: Integration tests for postmortem path (AC #1, #2, #5)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestPostmortemOutputDir:
    """Testes de integração: postmortem salvo em diretório customizado."""

    def test_postmortem_saved_to_custom_directory(
        self, pipeline_result, simulated_incident, tmp_path,
    ) -> None:
        """Postmortem salvo em diretório customizado via output_dir."""
        from main import step_postmortem

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path, _ = step_postmortem(
                pipeline_result, simulated_incident,
                output_dir=str(tmp_path),
            )

        assert path.exists(), f"Postmortem não encontrado em {path}"
        assert path.parent == tmp_path, f"Postmortem não está em tmp_path: {path}"
        assert path.stat().st_size > 0, "Postmortem vazio"
        assert path.name.startswith("postmortem_"), f"Nome inesperado: {path.name}"

    def test_postmortem_creates_nested_directory(
        self, pipeline_result, simulated_incident, tmp_path,
    ) -> None:
        """Diretórios aninhados são auto-criados (parents=True)."""
        from main import step_postmortem

        nested = tmp_path / "nested" / "deep" / "dir"
        assert not nested.exists()

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path, _ = step_postmortem(
                pipeline_result, simulated_incident,
                output_dir=str(nested),
            )

        assert nested.exists(), f"Diretório aninhado não criado: {nested}"
        assert path.exists(), f"Postmortem não encontrado em {path}"
        assert path.parent == nested

    def test_postmortem_default_output_dir(
        self, pipeline_result, simulated_incident,
    ) -> None:
        """Sem output_dir, postmortem é salvo em 'output/' (backward-compatible)."""
        from main import step_postmortem
        from pathlib import Path

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path, _ = step_postmortem(pipeline_result, simulated_incident)

        assert path.exists(), f"Postmortem não encontrado em {path}"
        assert path.parent == Path("output"), f"Postmortem não em output/: {path}"
        # Cleanup: remove o arquivo gerado em output/
        path.unlink(missing_ok=True)
