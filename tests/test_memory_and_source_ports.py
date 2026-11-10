"""Portas para a memória institucional e para as fontes de telemetria (A4/ARQ-16).

P3-3 (docs/analise-20261109.md):
- IncidentMemoryDB (SQLite) não tinha porta; os agentes dependiam da classe
  concreta;
- as portas de fontes externas (MetricsSource, LogSource, TraceSource,
  IncidentTracker) viviam em tools/interfaces.py, paralelas a
  application/ports.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parent.parent


def test_sqlite_memory_satisfies_the_port(tmp_path):
    from application.ports.incident_memory import IncidentMemory
    from rag.memory import IncidentMemoryDB

    assert isinstance(IncidentMemoryDB(db_path=str(tmp_path / "m.db")), IncidentMemory)


def test_source_ports_live_in_application_and_are_reexported():
    import application.ports.incident_tracker as tracker_port
    import application.ports.telemetry_sources as ports
    import tools.interfaces as legacy

    assert legacy.MetricsSource is ports.MetricsSource
    assert legacy.LogSource is ports.LogSource
    assert legacy.TraceSource is ports.TraceSource
    assert legacy.IncidentTracker is tracker_port.IncidentTracker


@pytest.mark.parametrize("module", ["agents/orchestrator.py", "agents/skill_router.py",
                                    "agents/skill_evolver.py"])
def test_agents_depend_on_the_port_not_the_sqlite_class(module):
    tree = ast.parse((ROOT / module).read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "rag.memory"
        for alias in node.names
    }
    assert "IncidentMemoryDB" not in imported
