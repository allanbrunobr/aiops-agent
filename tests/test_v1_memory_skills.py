"""P4b (F5-4): memória episódica + skill learning no V1.

V1 persiste IncidentRecord, roteia skill e grava SkillOutcome (+ evolve) como o
V0 (step_persist_incident / step_skill_learning). Best-effort: falha de
persistência não muda o exit code. O conftest isola IncidentMemoryDB() em
tmp_path, então ``IncidentMemoryDB()`` no teste lê o mesmo arquivo do run.
"""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import patch

import pytest

from interfaces.cli.exit_codes import EXIT_APPROVAL_NEEDED
from rag.memory import IncidentMemoryDB

pytestmark = pytest.mark.unit


def _args(tmp_path, **over) -> Namespace:
    base = dict(
        config=None,
        no_llm=True,
        no_rag=True,
        compact=True,
        scenario=None,
        output_format="markdown",
        output_dir=str(tmp_path / "out"),
        data_source="simulated",
        tracker=None,
        rebuild_rag=False,
    )
    base.update(over)
    return Namespace(**base)


def _count(table: str) -> int:
    with IncidentMemoryDB()._connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608


def _run(tmp_path, **over) -> int:
    from interfaces.cli.v1_runner import run_v1

    return run_v1(_args(tmp_path, **over))


def test_v1_persists_incident_record(tmp_path):
    assert _count("incidents") == 0
    assert _run(tmp_path) == EXIT_APPROVAL_NEEDED
    assert _count("incidents") == 1
    with IncidentMemoryDB()._connect() as conn:
        row = conn.execute("SELECT * FROM incidents").fetchone()
    assert row["root_cause"]
    assert row["tags"] and row["tags"] != "[]"


def test_v1_routes_skill_and_writes_outcome_no_llm(tmp_path):
    _run(tmp_path)
    assert _count("diagnostic_skills") >= 1  # seed_default_skills (READ+ROUTE)
    assert _count("skill_outcomes") == 1  # WRITE funciona em --no-llm
    with IncidentMemoryDB()._connect() as conn:
        out = conn.execute("SELECT * FROM skill_outcomes").fetchone()
        skill = conn.execute(
            "SELECT * FROM diagnostic_skills WHERE skill_id = ?", (out["skill_id"],)
        ).fetchone()
    assert skill is not None
    # Sem LLM não há sinal para aprender: EMA não é atualizada (F16)
    assert skill["total_uses"] == 0


def test_second_run_accumulates(tmp_path):
    _run(tmp_path)
    _run(tmp_path)
    assert _count("incidents") == 2
    assert _count("skill_outcomes") == 2


def test_use_llm_flag_reaches_skill_learning(tmp_path):
    from blueprint.adapters import pipeline_nodes

    calls: dict = {}

    def fake_learning(memory_db, skill, **kw):
        calls.update(kw)
        calls["skill_id"] = skill.skill_id

    with patch.object(pipeline_nodes, "learn_skills_step", fake_learning):
        # no_llm=False sem chave: o V1 cai no fallback heurístico, mas o
        # learning recebe use_llm=True (EMA só roda nesse caso).
        _run(tmp_path, no_llm=False)
    assert calls["use_llm"] is True
    assert calls["skill_id"]


def test_learn_skills_step_ema_only_with_llm():
    from agents.skill_router import SkillRouter
    from blueprint.adapters.pipeline_nodes import learn_skills_step

    db = IncidentMemoryDB()
    router = SkillRouter(db)
    router.seed_default_skills()
    skill = router.route(["timeout"], ["svc"])
    kw = dict(
        tnr_result="success",
        reflection={"score": 0.9, "lesson": "ok"},
        tools_used=["query_logs"],
        iteration_count=2,
        client=None,
    )
    learn_skills_step(db, skill, use_llm=False, **kw)
    assert db.get_skill(skill.skill_id).total_uses == 0
    learn_skills_step(db, skill, use_llm=True, **kw)
    assert db.get_skill(skill.skill_id).total_uses == 1
    assert len(db.get_skill_outcomes(skill.skill_id)) == 2


def test_persistence_failure_does_not_change_exit_code(tmp_path):
    with patch.object(
        IncidentMemoryDB, "save_incident", side_effect=RuntimeError("disk full")
    ):
        assert _run(tmp_path) == EXIT_APPROVAL_NEEDED
    assert _count("incidents") == 0


def test_skill_learning_failure_does_not_change_exit_code(tmp_path):
    with patch.object(
        IncidentMemoryDB, "save_skill_outcome", side_effect=RuntimeError("boom")
    ):
        assert _run(tmp_path) == EXIT_APPROVAL_NEEDED
    assert _count("incidents") == 1  # incidente persiste mesmo se skills falham


def test_no_incident_when_no_root_cause():
    from blueprint.adapters.pipeline_nodes import persist_incident_step

    db = IncidentMemoryDB()
    assert not persist_incident_step(db, {"stages": {"diagnosis": {}}}, None, "x", 1.0)
    assert _count("incidents") == 0
