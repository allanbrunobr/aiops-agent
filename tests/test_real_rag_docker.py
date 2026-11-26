"""A3: artefatos do caminho container (linux/amd64) para os testes real-RAG.

Checagem estática: docker não roda em CI unit nem no M5. Garante que o
Dockerfile e o script não percam a plataforma amd64 nem o `--frozen`, e que o
caminho nativo no Intel-Mac não seja reintroduzido.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "docker" / "real-rag.Dockerfile"
SCRIPT = ROOT / "scripts" / "real-rag-docker.sh"
DOC = ROOT / "docs" / "testing.md"

pytestmark = pytest.mark.unit


def _text(path: Path) -> str:
    assert path.is_file(), f"{path.relative_to(ROOT)} ausente"
    return path.read_text(encoding="utf-8")


def test_dockerfile_amd64_frozen_e_real_rag():
    txt = _text(DOCKERFILE)
    assert re.search(r"^FROM --platform=linux/amd64 python:3\.12-slim", txt, re.M)
    assert "uv sync --frozen" in txt
    # maturin/Rust não existe na imagem: não instalar o próprio projeto.
    assert "--no-install-project" in txt
    assert "COPY pyproject.toml uv.lock" in txt
    assert re.search(r'CMD \["python", "-m", "pytest", "tests/", "-m", "real_rag"', txt)


def test_script_build_amd64_run_e_args_extras():
    txt = _text(SCRIPT)
    assert txt.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in txt
    assert "build --platform linux/amd64" in txt
    assert "docker/real-rag.Dockerfile" in txt
    assert "run --rm --platform linux/amd64" in txt
    assert '"$@"' in txt  # args extras de pytest
    assert os.access(SCRIPT, os.X_OK), "script sem bit de execução"


def test_script_sintaxe_bash_valida():
    _text(SCRIPT)
    r = subprocess.run(
        ["bash", "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert r.returncode == 0, r.stderr


def test_docs_documentam_tres_caminhos():
    txt = _text(DOC)
    assert "scripts/real-rag-docker.sh" in txt
    assert ".venv/bin/python -m pytest tests/ -m real_rag" in txt
    assert "ubuntu" in txt and "Intel" in txt and "arm64" in txt
