"""Regra de dependências entre camadas, verificada por AST (A6/ARQ-15).

P3-3 (docs/analise-20261109.md): a regra existia só em docstring, sem portão,
e já era violada — um agente importava um adapter concreto
(agents/diagnoser.py → adapters.llm) e tools/ importava interfaces/.
O import-linter no CI exigiria mexer em .github/; este teste roda nos jobs
existentes e falha com a lista de violações.
"""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent

# camada → pacotes internos que ela NÃO pode importar
FORBIDDEN: dict[str, set[str]] = {
    # Domínio puro (ADR 0003): nada interno além de domain.
    "domain": {"application", "adapters", "agents", "app", "auth", "blueprint", "config",
               "core", "data", "infra", "interfaces", "pipeline", "rag", "safety", "tools",
               "validation"},
    # Portas: só domain.
    "application": {"adapters", "agents", "app", "auth", "blueprint", "config", "core",
                    "data", "infra", "interfaces", "pipeline", "rag", "safety", "tools",
                    "validation"},
    # Núcleo: nunca adapters concretos, entrypoints ou composição.
    "agents": {"adapters", "app", "interfaces", "infra", "blueprint"},
    "pipeline": {"adapters", "app", "interfaces", "infra", "blueprint", "agents"},
    "rag": {"adapters", "app", "interfaces", "infra", "blueprint", "agents"},
    "safety": {"adapters", "app", "interfaces", "infra", "blueprint", "agents"},
    # Integrações externas não dependem da borda HTTP/MCP nem da composição.
    "tools": {"interfaces", "app", "infra", "blueprint"},
    "adapters": {"interfaces", "app", "infra", "blueprint", "agents"},
}


def _tracked_python_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    return [ROOT / f for f in out]


def _violations() -> list[str]:
    found = []
    for path in _tracked_python_files():
        layer = path.relative_to(ROOT).parts[0]
        if layer not in FORBIDDEN:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                modules = [node.module]
            else:
                continue
            for module in modules:
                if module.split(".")[0] in FORBIDDEN[layer]:
                    found.append(f"{path.relative_to(ROOT)}:{node.lineno} {layer} → {module}")
    return found


def test_no_layer_violations():
    violations = _violations()
    assert violations == [], "Violações da regra de camadas:\n  " + "\n  ".join(violations)
