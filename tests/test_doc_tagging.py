"""Testes de validacao para CONTRIBUTING.md.

Valida que CONTRIBUTING.md existe com o conteudo obrigatorio.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Diretorio raiz do projeto (relativo ao arquivo de testes)
PROJECT_ROOT = Path(__file__).parent.parent

# --- Testes do CONTRIBUTING.md ---


@pytest.mark.unit
def test_contributing_md_exists():
    """Verifica que CONTRIBUTING.md existe na raiz do projeto."""
    contributing = PROJECT_ROOT / "CONTRIBUTING.md"
    assert contributing.exists(), (
        f"CONTRIBUTING.md nao encontrado em {PROJECT_ROOT}"
    )


@pytest.mark.unit
def test_contributing_has_run_modes():
    """Verifica que CONTRIBUTING.md menciona os tres modos de execucao."""
    content = (PROJECT_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "--no-llm" in content, (
        "CONTRIBUTING.md deve mencionar o flag --no-llm"
    )
    assert "--no-rag" in content, (
        "CONTRIBUTING.md deve mencionar o flag --no-rag"
    )
    assert "python main.py" in content, (
        "CONTRIBUTING.md deve mencionar o comando padrao 'python main.py'"
    )


@pytest.mark.unit
def test_contributing_has_pr_workflow():
    """Verifica que CONTRIBUTING.md contem secoes sobre branching, testes e PR."""
    content = (PROJECT_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    # Verifica presenca de secoes-chave
    assert "branch" in content.lower(), (
        "CONTRIBUTING.md deve mencionar branching convention"
    )
    assert "test" in content.lower(), (
        "CONTRIBUTING.md deve mencionar test requirements"
    )
    assert "pr" in content.lower() or "pull request" in content.lower(), (
        "CONTRIBUTING.md deve mencionar PR workflow"
    )


@pytest.mark.unit
def test_contributing_has_code_conventions():
    """Verifica que CONTRIBUTING.md documenta convencoes de codigo."""
    content = (PROJECT_ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")

    assert "from __future__ import annotations" in content, (
        "CONTRIBUTING.md deve documentar a regra de __future__ annotations"
    )
    assert "dataclass" in content.lower(), (
        "CONTRIBUTING.md deve mencionar uso de dataclasses"
    )

