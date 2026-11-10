"""Um só carregador de `.env.local` (M3/F5-10).

P3-6 (docs/analise-20261109.md): o parser artesanal estava copiado em 5
lugares; a cópia da demo não fazia strip antes de testar '#', e nenhuma tratava
aspas nem `export KEY=v`. Um valor entre aspas chegava com as aspas ao cliente
Slack/Anthropic.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent

# Capturado na coleta (import deste módulo), fora de qualquer fixture: é o
# valor que um `from interfaces.demo.server import app` no topo de um arquivo
# de teste usa.
import config.env as _config_env  # noqa: E402

REPO_ROOT_AT_COLLECTION = _config_env.REPO_ROOT
KEYS = ("AIOPS_T_QUOTED", "AIOPS_T_SINGLE", "AIOPS_T_EXPORT", "AIOPS_T_PLAIN",
        "AIOPS_T_PRESET", "AIOPS_T_BOTH", "AIOPS_T_ONLY_ENV")


@pytest.fixture
def clean_env(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("# AIOPS_T_COMMENTED", raising=False)
    yield
    for key in KEYS:
        os.environ.pop(key, None)


def test_parses_quotes_export_and_indented_comments(tmp_path, clean_env, monkeypatch):
    from config.env import load_local_env

    (tmp_path / ".env.local").write_text(
        'AIOPS_T_QUOTED="xoxb-123"\n'
        "AIOPS_T_SINGLE='abc'\n"
        "export AIOPS_T_EXPORT=exported\n"
        "  # AIOPS_T_COMMENTED=1\n"
        "AIOPS_T_PLAIN = plain \n"
        "AIOPS_T_PRESET=from-file\n"
    )
    monkeypatch.setenv("AIOPS_T_PRESET", "from-process")

    assert load_local_env(tmp_path) is True
    assert os.environ["AIOPS_T_QUOTED"] == "xoxb-123"
    assert os.environ["AIOPS_T_SINGLE"] == "abc"
    assert os.environ["AIOPS_T_EXPORT"] == "exported"
    assert os.environ["AIOPS_T_PLAIN"] == "plain"
    assert "# AIOPS_T_COMMENTED" not in os.environ
    # Variável já presente no processo não é sobrescrita.
    assert os.environ["AIOPS_T_PRESET"] == "from-process"


def test_env_local_wins_over_env(tmp_path, clean_env):
    from config.env import load_local_env

    (tmp_path / ".env").write_text("AIOPS_T_BOTH=from-env\nAIOPS_T_ONLY_ENV=env\n")
    (tmp_path / ".env.local").write_text("AIOPS_T_BOTH=from-env-local\n")

    load_local_env(tmp_path)
    assert os.environ["AIOPS_T_BOTH"] == "from-env-local"
    assert os.environ["AIOPS_T_ONLY_ENV"] == "env"


def test_missing_files_are_not_an_error(tmp_path, clean_env):
    from config.env import load_local_env

    assert load_local_env(tmp_path) is False


def test_suite_never_reads_the_checkout_env_files(clean_env):
    """O conftest aponta a raiz padrão para um diretório vazio em todo teste."""
    from config.env import REPO_ROOT, load_local_env

    assert REPO_ROOT != ROOT
    assert load_local_env() is False


def test_collection_never_reads_the_checkout_env_files():
    """Imports no nível de módulo (coleta) também não leem o checkout."""
    assert REPO_ROOT_AT_COLLECTION != ROOT
    assert not (REPO_ROOT_AT_COLLECTION / ".env.local").exists()
    assert not (REPO_ROOT_AT_COLLECTION / ".env").exists()


def test_no_hand_written_env_parser_left():
    """Portão: ninguém volta a parsear .env.local à mão fora de config/env.py."""
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, capture_output=True,
                           text=True, check=True).stdout.split()
    offenders = []
    for rel in files:
        if rel.startswith("tests/") or rel == "config/env.py":
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        if re.search(r"\.env(\.local)?['\"]", text) and re.search(r"read_text\(\)\.splitlines\(\)", text):
            offenders.append(rel)
    assert offenders == [], "Parser manual de .env ainda presente em: " + ", ".join(offenders)
