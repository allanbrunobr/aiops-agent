"""A documentação não manda o leitor para o que não existe (M1/M2, F5-05/07/13/14).

P3-5 (docs/analise-20261109.md): comandos `uvicorn` para módulos sem `app`,
cenários inexistentes, prefixo de variável de ambiente errado (AIOPS__) e links
para arquivos que não existem. Este teste verifica, sem rede:
- todo link relativo de markdown aponta para um arquivo do repositório;
- todo `uvicorn pacote.modulo:objeto` aponta para um objeto definido no módulo;
- todo `--scenario <nome>` é um cenário registrado;
- nenhuma variável usa o prefixo `AIOPS__` (o loader usa `AIOPS_` + seções com `__`).
Relatórios datados (analise-*, p*-progresso) registram o estado de uma data e
ficam de fora.
"""
from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
DATED = re.compile(r"docs/(analise-\d+|p\d-progresso)\.md$")


def _docs() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    return [ROOT / f for f in out if not DATED.search(f)]


def _without_code_fences(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


def test_relative_links_resolve():
    broken = []
    for doc in _docs():
        for target in re.findall(r"\]\(([^)\s]+)\)", _without_code_fences(doc.read_text())):
            if re.match(r"^[a-z]+:|^#|^mailto:", target):
                continue
            path = target.split("#")[0]
            if not path:
                continue
            resolved = (doc.parent / path.replace("%20", " ")).resolve()
            if not resolved.exists():
                broken.append(f"{doc.relative_to(ROOT)} → {target}")
    assert broken == [], "Links quebrados:\n  " + "\n  ".join(broken)


def _defines(module_path: Path, name: str) -> bool:
    tree = ast.parse(module_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
            return True
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return True
    return False


def test_uvicorn_targets_exist():
    missing = []
    for doc in _docs():
        for module, attr in re.findall(r"uvicorn\s+([\w.]+):(\w+)", doc.read_text()):
            base = ROOT / module.replace(".", "/")
            candidates = [base.with_suffix(".py"), base / "__init__.py"]
            path = next((c for c in candidates if c.is_file()), None)
            if path is None or not _defines(path, attr):
                missing.append(f"{doc.relative_to(ROOT)}: uvicorn {module}:{attr}")
    assert missing == [], "Alvos de uvicorn inexistentes:\n  " + "\n  ".join(missing)


def test_scenarios_mentioned_exist():
    # Os cenários se registram ao importar seus módulos (como em main.py).
    import data.memory_leak_scenario  # noqa: F401
    import data.sample_incident  # noqa: F401
    import data.unknown_fault_scenario  # noqa: F401
    from data.registry import list_scenarios

    known = set(list_scenarios())
    unknown = []
    for doc in _docs():
        for line in doc.read_text().splitlines():
            if "--scenario" not in line:
                continue
            names = re.findall(r"--scenario[ =]([a-z0-9]+(?:-[a-z0-9]+)+)", line)
            # Nomes listados entre crases na mesma linha (ex.: `memory-leak`).
            names += re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`", line)
            for name in names:
                if name not in known:
                    unknown.append(f"{doc.relative_to(ROOT)}: {name}")
    assert unknown == [], f"Cenários inexistentes (registrados: {sorted(known)}):\n  " + "\n  ".join(unknown)


def test_no_double_underscore_env_prefix():
    wrong = []
    for doc in _docs():
        for lineno, line in enumerate(doc.read_text().splitlines(), 1):
            if re.search(r"\bAIOPS__", line):
                wrong.append(f"{doc.relative_to(ROOT)}:{lineno}")
    assert wrong == [], "Prefixo AIOPS__ (o correto é AIOPS_SECAO__CAMPO):\n  " + "\n  ".join(wrong)
