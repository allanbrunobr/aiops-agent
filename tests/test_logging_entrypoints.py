"""Redação de segredos ativa em todos os pontos de entrada (M4/F5-06).

P3-6 (docs/analise-20261109.md): `configure_logging()` só era chamado em
main_v1. Em main() (V0), MCP, demo e webhook o processador de redação não
rodava. E main.py tinha sua própria lista de padrões (4 regex) contra 6 em
core/logging.py: `_sanitize_secrets("xoxb-...")` devolvia o token intacto, e
essa função limpa a mensagem de erro enviada ao Slack em `pipeline.failed`.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
import structlog

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parent.parent
SLACK_TOKEN = "xoxb-" + "1234567890-abcdefghij"
BEARER = "Bearer " + "abcdefghijklmnopqrstuvwxyz012345"


def test_cli_sanitizer_uses_the_single_pattern_list():
    import core.logging as core_logging
    import main

    assert main.API_KEY_PATTERNS is core_logging.SECRET_PATTERNS
    assert SLACK_TOKEN not in main._sanitize_secrets(f"falhou com {SLACK_TOKEN}")
    assert BEARER not in main._sanitize_secrets(f"header {BEARER}")


def test_configured_logging_redacts_values(capsys):
    from core.logging import configure_logging

    configure_logging("INFO")
    structlog.get_logger("t").warning("x", detail=f"token {SLACK_TOKEN}")
    line = capsys.readouterr().err.strip().splitlines()[-1]
    assert SLACK_TOKEN not in line
    assert json.loads(line)["detail"] == "token [REDACTED]"


def _calls_configure_logging(node: ast.AST) -> bool:
    return any(
        isinstance(n, ast.Call)
        and getattr(n.func, "id", getattr(n.func, "attr", None)) == "configure_logging"
        for n in ast.walk(node)
    )


def _function(tree: ast.Module, name: str) -> ast.AST:
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def _main_guard(tree: ast.Module) -> ast.AST:
    return next(
        n for n in tree.body
        if isinstance(n, ast.If) and "__main__" in ast.unparse(n.test)
    )


@pytest.mark.parametrize("path,where", [
    ("main.py", "main"),
    ("main.py", "main_v1"),
    ("interfaces/demo/server.py", "main"),
    ("interfaces/mcp/server.py", "__main__"),
    ("infra/run_webhook_server.py", "__main__"),
])
def test_entrypoint_configures_logging(path, where):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
    node = _main_guard(tree) if where == "__main__" else _function(tree, where)
    assert _calls_configure_logging(node), f"{path}:{where} não chama configure_logging()"
