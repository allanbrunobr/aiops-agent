"""F5-3: loader único de .env/.env.local (config/env.py).

Substitui cinco parsers manuais (CLI V0, CLI V1, demo, webhook server, script
de Slack) que divergiam entre si. Contrato (pós-merge, ver
tests/test_local_env_loader.py): ambiente do processo > .env.local > .env;
nenhum arquivo sobrescreve variável já definida. `.env.local` são overrides
locais e vence o `.env` compartilhado — a ordem inversa do V0 era um legado
que tornava o arquivo de overrides inútil.
"""

from __future__ import annotations

import os

import pytest

from config.env import ENV_FILES, load_local_env, project_root

_KEYS = ("AIOPS_T_A", "AIOPS_T_B", "AIOPS_T_C")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in _KEYS:
        monkeypatch.delenv(k, raising=False)


@pytest.mark.unit
def test_loads_env_local_then_env_without_override(tmp_path):
    (tmp_path / ".env").write_text("AIOPS_T_A=from_env\n")
    (tmp_path / ".env.local").write_text("AIOPS_T_A=from_local\nAIOPS_T_B=only_local\n")

    loaded = load_local_env(tmp_path)

    assert loaded is True
    assert ENV_FILES == (".env.local", ".env")
    # .env.local é carregado primeiro e .env não sobrescreve (override=False):
    # overrides locais vencem o .env compartilhado.
    assert os.environ["AIOPS_T_A"] == "from_local"
    assert os.environ["AIOPS_T_B"] == "only_local"


@pytest.mark.unit
def test_process_environment_always_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("AIOPS_T_A", "from_shell")
    (tmp_path / ".env.local").write_text("AIOPS_T_A=from_file\n")

    load_local_env(tmp_path)

    assert os.environ["AIOPS_T_A"] == "from_shell"


@pytest.mark.unit
def test_missing_files_are_a_noop(tmp_path):
    assert load_local_env(tmp_path) is False
    assert "AIOPS_T_A" not in os.environ


@pytest.mark.unit
def test_comments_blank_lines_and_quotes(tmp_path):
    (tmp_path / ".env.local").write_text(
        '# comentário\n\nAIOPS_T_A = spaced \nAIOPS_T_C="quoted value"\n'
    )

    load_local_env(tmp_path)

    assert os.environ["AIOPS_T_A"] == "spaced"
    # python-dotenv remove aspas (o parser manual as mantinha — melhoria
    # intencional, documentada em docs/improvement-progress.md)
    assert os.environ["AIOPS_T_C"] == "quoted value"


@pytest.mark.unit
def test_default_root_is_repo_root():
    root = project_root()
    assert (root / "config" / "defaults.yaml").is_file()
    assert (root / "main.py").is_file()
