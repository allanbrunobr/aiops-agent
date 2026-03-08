"""Testes de validacao para CONTRIBUTING.md e tagging de documentos aspiracionais.

Valida que CONTRIBUTING.md existe com conteudo obrigatorio e que os 4 documentos
grandes (SRE, Platform, Security, API Contracts) possuem tags de status em todas
as secoes numeradas.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# Diretorio raiz do projeto (relativo ao arquivo de testes)
PROJECT_ROOT = Path(__file__).parent.parent

# Tags validas para secoes de documentacao
VALID_TAGS = {"[IMPLEMENTED]", "[NEXT SPRINT]", "[NORTH STAR]", "[DEPRECATED]"}
TAG_PATTERN = re.compile(r"\[(IMPLEMENTED|NEXT SPRINT|NORTH STAR|DEPRECATED)\]")

# Padroes de heading numerado (## 1. ou ### 2.1 — nivel 2 e 3 apenas)
NUMBERED_HEADING = re.compile(r"^(#{2,3})\s+(\d+\.?\d*\.?\d*)\s+")

# Os 4 documentos grandes que devem ter tags
TAGGED_DOCS = [
    "docs/SRE-INTEGRATION-GUIDE.md",
    "docs/PLATFORM-INTEGRATION-ARCHITECTURE.md",
    "docs/SECURITY-COMPLIANCE-GUIDE.md",
    "docs/API-CONTRACTS-OBSERVABILITY.md",
]


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


# --- Testes de tagging dos documentos ---


@pytest.mark.unit
@pytest.mark.parametrize("doc_path", TAGGED_DOCS)
def test_docs_all_sections_tagged(doc_path: str):
    """Verifica que todas as secoes numeradas de cada doc possuem uma tag valida."""
    full_path = PROJECT_ROOT / doc_path
    assert full_path.exists(), f"Documento nao encontrado: {doc_path}"

    content = full_path.read_text(encoding="utf-8")
    lines = content.split("\n")

    untagged = []
    for i, line in enumerate(lines, start=1):
        # So verificar headings numerados (## 1., ### 2.1, etc.)
        if not NUMBERED_HEADING.match(line):
            continue

        # Verificar se tem uma tag valida
        if not TAG_PATTERN.search(line):
            untagged.append(f"  Linha {i}: {line.strip()}")

    assert not untagged, (
        f"Secoes sem tag em {doc_path}:\n" + "\n".join(untagged[:10])
    )


@pytest.mark.unit
def test_implemented_tags_match_codebase():
    """Spot-check: secoes marcadas [IMPLEMENTED] possuem codigo correspondente."""
    checks = [
        # (doc, secao que deve ser IMPLEMENTED, arquivo de codigo que deve existir)
        (
            "docs/SRE-INTEGRATION-GUIDE.md",
            "1. Respostas Diretas",
            "data/models.py",
        ),
        (
            "docs/SECURITY-COMPLIANCE-GUIDE.md",
            "7.1 Arquitetura",
            "pipeline/telemetry_sanitizer.py",
        ),
        (
            "docs/API-CONTRACTS-OBSERVABILITY.md",
            "5.1 LogEntry",
            "data/models.py",
        ),
        (
            "docs/API-CONTRACTS-OBSERVABILITY.md",
            "5.2 MetricPoint",
            "data/models.py",
        ),
        (
            "docs/SECURITY-COMPLIANCE-GUIDE.md",
            "1.3 Analise STRIDE",
            "pipeline/telemetry_sanitizer.py",
        ),
    ]

    for doc_path, section_hint, code_path in checks:
        # Verificar que o arquivo de codigo existe
        code_file = PROJECT_ROOT / code_path
        assert code_file.exists(), (
            f"Codigo {code_path} nao encontrado (referenciado por {doc_path} secao {section_hint})"
        )

        # Verificar que a secao tem tag [IMPLEMENTED]
        content = (PROJECT_ROOT / doc_path).read_text(encoding="utf-8")
        # Procurar linha com o hint da secao
        found = False
        for line in content.split("\n"):
            if section_hint in line and line.startswith("#"):
                assert "[IMPLEMENTED]" in line, (
                    f"Secao '{section_hint}' em {doc_path} deveria ter tag [IMPLEMENTED] "
                    f"pois {code_path} existe. Linha: {line.strip()}"
                )
                found = True
                break
        assert found, (
            f"Secao contendo '{section_hint}' nao encontrada em {doc_path}"
        )


@pytest.mark.unit
def test_north_star_tags_no_code():
    """Spot-check: secoes aspiracionais NAO possuem codigo implementado."""
    checks = [
        # (doc, secao que deve ser NORTH STAR, classe que NAO deve existir em models.py)
        (
            "docs/API-CONTRACTS-OBSERVABILITY.md",
            "5.4 Alert",
            "Alert",
        ),
        (
            "docs/API-CONTRACTS-OBSERVABILITY.md",
            "5.5 Incident",
            "Incident",
        ),
        (
            "docs/PLATFORM-INTEGRATION-ARCHITECTURE.md",
            "3.1 O que e MCP",
            None,
        ),
    ]

    models_content = (PROJECT_ROOT / "data" / "models.py").read_text(encoding="utf-8")

    for doc_path, section_hint, absent_class in checks:
        content = (PROJECT_ROOT / doc_path).read_text(encoding="utf-8")
        found = False
        for line in content.split("\n"):
            if section_hint in line and line.startswith("#"):
                assert "[IMPLEMENTED]" not in line, (
                    f"Secao '{section_hint}' em {doc_path} marcada [IMPLEMENTED] "
                    f"mas nao possui codigo correspondente. Linha: {line.strip()}"
                )
                found = True
                break
        assert found, (
            f"Secao contendo '{section_hint}' nao encontrada em {doc_path}"
        )

        # Verificar que a classe realmente nao existe
        if absent_class:
            assert f"class {absent_class}" not in models_content, (
                f"Classe '{absent_class}' encontrada em data/models.py mas secao "
                f"'{section_hint}' em {doc_path} esta marcada como aspiracional"
            )


@pytest.mark.unit
def test_no_invalid_tags():
    """Verifica que nenhum documento usa tags fora do conjunto valido."""
    # Regex para tags no final de headings (posicao onde tags sao colocadas)
    # Captura texto entre colchetes com 2+ caracteres maiusculos/espacos no fim da linha
    trailing_bracket_tag = re.compile(r"\[([A-Z][A-Z\s]+)\]\s*$")

    for doc_path in TAGGED_DOCS:
        content = (PROJECT_ROOT / doc_path).read_text(encoding="utf-8")
        for i, line in enumerate(content.split("\n"), start=1):
            if not line.startswith("#"):
                continue
            match = trailing_bracket_tag.search(line)
            if match:
                tag = f"[{match.group(1)}]"
                assert tag in VALID_TAGS, (
                    f"Tag invalida '{tag}' encontrada em {doc_path} linha {i}: "
                    f"{line.strip()}"
                )
