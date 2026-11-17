"""F5-3: loader único de .env/.env.local (config/env.py).

Substitui cinco parsers manuais (CLI V0, CLI V1, demo, webhook server, script
de Slack) que divergiam entre si. Contrato preservado do CLI V0:
ambiente do processo > .env > .env.local; nenhum arquivo sobrescreve variável
já definida.
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
def test_loads_env_then_env_local_without_override(tmp_path):
    (tmp_path / ".env").write_text("AIOPS_T_A=from_env\n")
    (tmp_path / ".env.local").write_text("AIOPS_T_A=from_local\nAIOPS_T_B=only_local\n")

    loaded = load_local_env(tmp_path)

    assert [p.name for p in loaded] == list(ENV_FILES)
    # .env é carregado primeiro e .env.local não sobrescreve (semântica do V0:
    # load_dotenv() + setdefault)
    assert os.environ["AIOPS_T_A"] == "from_env"
    assert os.environ["AIOPS_T_B"] == "only_local"


@pytest.mark.unit
def test_process_environment_always_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("AIOPS_T_A", "from_shell")
    (tmp_path / ".env.local").write_text("AIOPS_T_A=from_file\n")

    load_local_env(tmp_path)

    assert os.environ["AIOPS_T_A"] == "from_shell"


@pytest.mark.unit
def test_missing_files_are_a_noop(tmp_path):
    assert load_local_env(tmp_path) == []
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
