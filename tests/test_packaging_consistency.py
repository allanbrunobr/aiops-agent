"""Uma lista de dependências declarada, e a do CI não diverge dela (M5/F5-15).

P3-7 (docs/analise-20261109.md): pyproject.toml não tinha `[project]` nem
requires-python; requirements.txt (entrada do CI) punha PyJWT, cryptography e
httpx — importados por auth/ e agents/ — sob "# Testing". O CI continua
instalando requirements.txt (mudar .github/ é decisão do Allan); este teste
garante que ele é exatamente pyproject `[project].dependencies` + grupos.
"""
from __future__ import annotations

import ast
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent


def _norm(req: str) -> str:
    name, rest = re.match(r"^\s*([A-Za-z0-9_.-]+(?:\[[^\]]+\])?)\s*(.*)$", req).groups()
    return name.lower().replace("_", "-") + rest.replace(" ", "")


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def _requirements() -> set[str]:
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    return {_norm(ln) for ln in lines if ln.strip() and not ln.lstrip().startswith("#")}


def test_project_metadata_pins_python():
    project = _pyproject()["project"]
    assert project["requires-python"] == ">=3.12,<3.13"
    assert (ROOT / ".python-version").read_text().strip() == "3.12"


def test_requirements_txt_is_exactly_pyproject():
    data = _pyproject()
    declared = {_norm(d) for d in data["project"]["dependencies"]}
    for group in data["dependency-groups"].values():
        declared |= {_norm(d) for d in group}
    assert _requirements() == declared


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("module,dist", [("jwt", "pyjwt"), ("cryptography", "cryptography"),
                                         ("httpx", "httpx")])
def test_packages_imported_by_runtime_code_are_runtime_dependencies(module, dist):
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    used = [f for f in files if not f.startswith("tests/")
            and module in _top_level_imports(ROOT / f)]
    assert used, f"{module} não é mais importado fora de tests/"
    runtime = {_norm(d).split(">")[0].split("<")[0].split("[")[0]
               for d in _pyproject()["project"]["dependencies"]}
    assert dist in runtime, f"{module} é usado em {used[:3]} mas não está em [project].dependencies"
