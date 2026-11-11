"""`create_app` tem um entrypoint real; o DI global que nunca funcionou saiu (A5).

P3-2 (docs/analise-20261109.md, ARQ-09/F5-04): nenhum processo servia
`create_app` (HTTP/A2A/OAuth/MCP-SSE). E havia dois mecanismos de DI: o
`request.app.state`, usado por A2A, webhook e accuracy, e um `app_state` global
+ `interfaces/http/dependencies.py` + um `knowledge_store` global que nada
populava (os asserts de dependencies.py sempre falhariam).
"""
from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def captured_run(monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    return calls


def test_entrypoint_serves_create_app_on_localhost(captured_run):
    from interfaces.http.__main__ import main

    main([])
    (app, kwargs), = captured_run
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 47893
    assert kwargs["log_config"] is None  # logs do uvicorn com redação
    assert app.title == "AIOps Agent"
    from fastapi.testclient import TestClient

    assert TestClient(app).get("/health").status_code == 200


def test_http_access_log_is_suppressed_on_purpose(captured_run):
    """Sem log_level, uvicorn.access fica em WARNING: o session_id do MCP SSE
    vai na query string e não deve parar em access log."""
    from interfaces.http.__main__ import main

    main([])
    (_, kwargs), = captured_run
    assert "log_level" not in kwargs


def test_demo_keeps_its_access_log(captured_run, monkeypatch):
    """A demo mostrava as linhas de acesso antes do log_config=None (pré-PR)."""
    import sys

    from interfaces.demo import server

    monkeypatch.setattr(sys, "argv", ["server"])
    server.main()
    (_, kwargs), = captured_run
    assert kwargs["log_config"] is None
    assert kwargs["log_level"] == "info"


@pytest.mark.parametrize(("kwargs", "level"), [({}, "WARNING"), ({"log_level": "info"}, "INFO")])
def test_log_level_decides_the_access_log(kwargs, level):
    """O que os dois testes acima conferem no argumento, medido no uvicorn:
    depois do configure_logging(), só log_level reabre o uvicorn.access."""
    import logging

    import uvicorn
    from fastapi import FastAPI

    from core.logging import configure_logging

    configure_logging()
    uvicorn.Config(FastAPI(), log_config=None, **kwargs)
    assert logging.getLevelName(logging.getLogger("uvicorn.access").level) == level


def test_entrypoint_accepts_host_port_and_config(captured_run, tmp_path):
    from interfaces.http.__main__ import main

    cfg = tmp_path / "c.yaml"
    cfg.write_text("thresholds:\n  sigma_threshold: 2.5\n")
    main(["--host", "0.0.0.0", "--port", "9000", "--config", str(cfg)])
    (app, kwargs), = captured_run
    assert (kwargs["host"], kwargs["port"]) == ("0.0.0.0", 9000)
    assert app.state.config.thresholds.sigma_threshold == 2.5


def test_entrypoint_configures_logging_and_loads_env():
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parent.parent
                      / "interfaces/http/__main__.py").read_text())
    called = {getattr(n.func, "id", getattr(n.func, "attr", None))
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert {"configure_logging", "load_local_env"} <= called


def test_global_app_state_di_is_gone():
    import interfaces.http.app as http_app

    assert not hasattr(http_app, "app_state")
    assert not hasattr(http_app, "AppState")
    assert not hasattr(http_app, "knowledge_store")
    assert importlib.util.find_spec("interfaces.http.dependencies") is None
