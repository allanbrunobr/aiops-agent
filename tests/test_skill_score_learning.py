"""O score de utilidade das skills é atualizado, e a evolução pode disparar (F16).

P3-9 (docs/analise-20261109.md): IncidentMemoryDB.update_skill_score nunca era
chamado em produção. utility_score e total_uses ficavam congelados, e
SkillEvolver.should_evolve (total_uses >= 3 e score < 0.3) nunca era verdade.
No modo --no-llm a "reflexão" é um placeholder (score 0.5): não há sinal, então
o score não muda nesse modo.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from rag.memory import DiagnosticSkill, IncidentMemoryDB

pytestmark = pytest.mark.unit


def _seed(db, score=0.31, uses=2):
    skill = DiagnosticSkill(skill_id="pool", name="pool", scenario_pattern="pool",
                            system_prompt="p", priority_tools=[], utility_score=score,
                            total_uses=uses)
    db.save_skill(skill)
    return skill


def _client():
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps({"new_system_prompt": "better", "rationale": "r"}))]
    client = MagicMock()
    client.create_message.return_value = msg
    return client


def test_llm_run_updates_score_and_uses(tmp_path):
    from main import step_skill_learning

    db = IncidentMemoryDB(db_path=str(tmp_path / "m.db"))
    skill = _seed(db, score=0.6, uses=0)
    step_skill_learning(db, skill, tnr_result="success", reflection={"score": 1.0, "lesson": "l"},
                        tools_used=["query_metrics"], iteration_count=2, use_llm=True, client=None)
    updated = db.get_skill("pool")
    assert updated.total_uses == 1
    assert updated.success_count == 1
    assert updated.utility_score == pytest.approx(0.3 * 1.0 + 0.7 * 0.6)


def test_pending_proposal_blocks_new_rewrites_of_the_default_skill(tmp_path):
    """A skill `default` é roteada sem olhar o score, então continua fraca.

    Sem trava, cada execução abaixo do limiar pagava uma chamada ao LLM e
    enfileirava mais uma proposta (medido no pré-PR: 4 pendentes em 6 rodadas).
    """
    from main import step_skill_learning
    from agents.skill_router import SkillRouter

    db = IncidentMemoryDB(db_path=str(tmp_path / "m.db"))
    router = SkillRouter(db)
    router.seed_default_skills()
    client = _client()
    for _ in range(6):
        skill = router.route(["unknown-thing"], ["svc"])
        assert skill.skill_id == "default"
        step_skill_learning(db, skill, tnr_result="not_verified",
                            reflection={"score": 0.25, "lesson": "x"}, tools_used=[],
                            iteration_count=1, use_llm=True, client=client)
    assert len(db.list_skill_proposals(status="pending")) == 1
    assert client.create_message.call_count == 1


def test_low_scores_trigger_a_pending_proposal(tmp_path):
    from main import step_skill_learning

    db = IncidentMemoryDB(db_path=str(tmp_path / "m.db"))
    skill = _seed(db, score=0.31, uses=2)
    proposal = step_skill_learning(db, skill, tnr_result="not_verified",
                                   reflection={"score": 0.0, "lesson": "missed"},
                                   tools_used=[], iteration_count=1, use_llm=True, client=_client())
    assert proposal is not None
    assert len(db.list_skill_proposals(status="pending")) == 1
    assert db.get_skill("pool").system_prompt == "p"  # ativa só depois de aprovar


def test_no_llm_run_does_not_move_the_score(tmp_path):
    from main import step_skill_learning

    db = IncidentMemoryDB(db_path=str(tmp_path / "m.db"))
    skill = _seed(db, score=0.6, uses=0)
    step_skill_learning(db, skill, tnr_result="not_verified",
                        reflection={"score": 0.5, "lesson": "no-llm mode"},
                        tools_used=[], iteration_count=0, use_llm=False, client=None)
    updated = db.get_skill("pool")
    assert updated.total_uses == 0
    assert updated.utility_score == pytest.approx(0.6)
