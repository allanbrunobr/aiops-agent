"""
Testes unitários para a Skill Evolution Layer — Estágio 1.

Cobre as 5 fases:
  1. READ    — similar_incidents injetados no contexto do DiagnosisAgent
  2. REFLECT — reflect() retorna lesson/score/priority_tools
  3. WRITE   — DiagnosticSkill + SkillOutcome persistidos no SQLite
  4. ROUTE   — SkillRouter seleciona a melhor skill por tipo de incidente
  5. EVOLVE  — SkillEvolver decide quando reescrever skills fracas
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from agents.diagnoser import DiagnosisAgent
from rag.memory import IncidentRecord


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_incident(scenario: str, root_cause: str) -> IncidentRecord:
    return IncidentRecord(
        incident_id="test-id",
        timestamp="2026-03-26T00:00:00+00:00",
        scenario=scenario,
        root_cause=root_cause,
        affected_services=["payment-svc"],
        diagnosis_confidence=0.9,
        resolution_time_sec=45.0,
        tags=["connection_pool"],
    )


# ---------------------------------------------------------------------------
# Fase 1: READ
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPhase1Read:
    """Fase 1: DiagnosisAgent injeta incidentes similares no contexto inicial."""

    def test_format_precedents_returns_string(self):
        """_format_precedents deve formatar lista de IncidentRecord como texto."""
        agent = DiagnosisAgent()
        incidents = [_make_incident("pool-exhaustion", "connection pool saturated")]
        result = agent._format_precedents(incidents)
        assert "connection pool saturated" in result or "pool-exhaustion" in result

    def test_format_precedents_empty_list(self):
        """_format_precedents com lista vazia deve retornar string vazia."""
        agent = DiagnosisAgent()
        result = agent._format_precedents([])
        assert isinstance(result, str)
        assert result == ""

    def test_init_accepts_similar_incidents(self):
        """DiagnosisAgent deve aceitar similar_incidents no construtor."""
        incidents = [_make_incident("pool-exhaustion", "pool saturated")]
        agent = DiagnosisAgent(similar_incidents=incidents)
        assert agent.similar_incidents == incidents

    def test_init_defaults_similar_incidents_to_empty(self):
        """Construtor sem similar_incidents deve ter lista vazia."""
        agent = DiagnosisAgent()
        assert agent.similar_incidents == []

    def test_multiple_precedents_truncated_to_3(self):
        """_format_precedents deve retornar no máximo 3 incidentes."""
        agent = DiagnosisAgent()
        incidents = [_make_incident(f"scenario-{i}", f"cause-{i}") for i in range(5)]
        result = agent._format_precedents(incidents)
        # Máximo 3 precedentes para não poluir o contexto
        count = result.count("cause-")
        assert count <= 3


# ---------------------------------------------------------------------------
# Fase 2: REFLECT
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPhase2Reflect:
    """Fase 2: DiagnosisAgent.reflect() retorna dict com lesson/score/priority_tools."""

    def test_reflect_fallback_no_client(self):
        """reflect() sem cliente LLM deve retornar fallback heurístico."""
        agent = DiagnosisAgent()
        # Sem API key — deve retornar fallback sem levantar exceção
        result = agent.reflect(
            diagnosis={"root_cause": "pool exhaustion"},
            tnr_result="success",
            tools_used=["query_metrics", "correlate_signals"],
        )
        assert "lesson" in result
        assert "score" in result
        assert "priority_tools" in result
        assert isinstance(result["score"], float)

    def test_reflect_returns_required_keys(self):
        """reflect() deve sempre retornar dict com lesson, score, priority_tools."""
        agent = DiagnosisAgent()
        result = agent.reflect(
            diagnosis={"root_cause": "memory leak"},
            tnr_result="regression",
            tools_used=["query_logs"],
        )
        for key in ("lesson", "score", "priority_tools"):
            assert key in result, f"Missing key: {key}"

    def test_reflect_score_is_float_in_range(self):
        """Score da reflexão deve ser float entre 0.0 e 1.0."""
        agent = DiagnosisAgent()
        result = agent.reflect(
            diagnosis={"root_cause": "config change"},
            tnr_result="success",
            tools_used=["classify_fault_type"],
        )
        assert 0.0 <= result["score"] <= 1.0

    def test_diagnoser_tracks_tools_used(self):
        """DiagnosisAgent deve expor tools_used e iteration_count após instanciação."""
        agent = DiagnosisAgent()
        assert hasattr(agent, "tools_used")
        assert hasattr(agent, "iteration_count")
        assert isinstance(agent.tools_used, list)
        assert isinstance(agent.iteration_count, int)
        assert agent.tools_used == []
        assert agent.iteration_count == 0


# ---------------------------------------------------------------------------
# Fase 3: WRITE — DiagnosticSkill + SkillOutcome
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPhase3SkillStore:
    """Fase 3: SkillStore persiste DiagnosticSkill e SkillOutcome no SQLite."""

    def test_diagnostic_skill_dataclass(self):
        """DiagnosticSkill deve ser importável de rag.memory com defaults corretos."""
        from rag.memory import DiagnosticSkill

        skill = DiagnosticSkill(
            skill_id="test-id",
            name="test-skill",
            scenario_pattern="connection pool",
            system_prompt="You are an SRE...",
            priority_tools=["query_metrics"],
        )
        assert skill.utility_score == 0.5
        assert skill.total_uses == 0
        assert skill.version == 1
        assert skill.success_count == 0

    def test_skill_outcome_dataclass(self):
        """SkillOutcome deve ser importável de rag.memory."""
        from rag.memory import SkillOutcome

        outcome = SkillOutcome(
            outcome_id="o1",
            skill_id="s1",
            incident_id="i1",
            tnr_passed=True,
            reflection_score=0.7,
            reflection_lesson="Pool correlation foi decisiva",
            tools_used=["query_metrics"],
            iteration_count=3,
            timestamp="2026-03-26T00:00:00+00:00",
        )
        assert outcome.tnr_passed is True
        assert outcome.reflection_score == 0.7

    def test_save_and_find_skill(self, tmp_path):
        """save_skill + find_best_skill devem persistir e recuperar."""
        from rag.memory import IncidentMemoryDB, DiagnosticSkill

        db = IncidentMemoryDB(db_path=str(tmp_path / "test.db"))
        skill = DiagnosticSkill(
            skill_id="s1",
            name="pool-diag",
            scenario_pattern="connection pool",
            system_prompt="Specialized pool prompt...",
            priority_tools=["query_metrics"],
            utility_score=0.8,
        )
        db.save_skill(skill)
        found = db.find_best_skill(["connection_pool"], ["payment-svc"])
        assert found is not None
        assert found.name == "pool-diag"
        assert abs(found.utility_score - 0.8) < 0.001

    def test_get_skill_by_id(self, tmp_path):
        """get_skill deve recuperar skill por ID."""
        from rag.memory import IncidentMemoryDB, DiagnosticSkill

        db = IncidentMemoryDB(db_path=str(tmp_path / "test.db"))
        skill = DiagnosticSkill(
            skill_id="s-get",
            name="test-get",
            scenario_pattern="memory leak",
            system_prompt="...",
            priority_tools=[],
        )
        db.save_skill(skill)
        found = db.get_skill("s-get")
        assert found is not None
        assert found.name == "test-get"

    def test_get_skill_returns_none_for_unknown(self, tmp_path):
        """get_skill deve retornar None para ID inexistente."""
        from rag.memory import IncidentMemoryDB

        db = IncidentMemoryDB(db_path=str(tmp_path / "test.db"))
        assert db.get_skill("nonexistent") is None

    def test_update_skill_score_ema(self, tmp_path):
        """update_skill_score deve aplicar EMA α=0.3."""
        from rag.memory import IncidentMemoryDB, DiagnosticSkill

        db = IncidentMemoryDB(db_path=str(tmp_path / "test.db"))
        skill = DiagnosticSkill(
            skill_id="s2",
            name="test",
            scenario_pattern=".*",
            system_prompt="...",
            priority_tools=[],
            utility_score=0.5,
        )
        db.save_skill(skill)
        db.update_skill_score("s2", tnr_passed=True, reflection_score=0.8)
        updated = db.get_skill("s2")
        assert updated is not None
        # EMA: 0.3 * 0.8 + 0.7 * 0.5 = 0.59
        assert abs(updated.utility_score - 0.59) < 0.001
        assert updated.total_uses == 1
        assert updated.success_count == 1

    def test_update_skill_score_tnr_failure(self, tmp_path):
        """update_skill_score com TNR failure não incrementa success_count."""
        from rag.memory import IncidentMemoryDB, DiagnosticSkill

        db = IncidentMemoryDB(db_path=str(tmp_path / "test.db"))
        skill = DiagnosticSkill(
            skill_id="s-fail",
            name="test",
            scenario_pattern=".*",
            system_prompt="...",
            priority_tools=[],
            utility_score=0.5,
        )
        db.save_skill(skill)
        db.update_skill_score("s-fail", tnr_passed=False, reflection_score=0.2)
        updated = db.get_skill("s-fail")
        assert updated is not None
        assert updated.total_uses == 1
        assert updated.success_count == 0
        # EMA: 0.3 * 0.2 + 0.7 * 0.5 = 0.41
        assert abs(updated.utility_score - 0.41) < 0.001

    def test_save_skill_outcome(self, tmp_path):
        """save_skill_outcome deve persistir SkillOutcome no banco."""
        from rag.memory import IncidentMemoryDB, DiagnosticSkill, SkillOutcome

        db = IncidentMemoryDB(db_path=str(tmp_path / "test.db"))
        skill = DiagnosticSkill(
            skill_id="s3",
            name="test",
            scenario_pattern=".*",
            system_prompt="...",
            priority_tools=[],
        )
        db.save_skill(skill)
        outcome = SkillOutcome(
            outcome_id="o1",
            skill_id="s3",
            incident_id="i1",
            tnr_passed=True,
            reflection_score=0.7,
            reflection_lesson="Pool correlation foi decisiva",
            tools_used=["query_metrics"],
            iteration_count=3,
            timestamp="2026-03-26T00:00:00+00:00",
        )
        db.save_skill_outcome(outcome)
        outcomes = db.get_skill_outcomes("s3", limit=5)
        assert len(outcomes) == 1
        assert outcomes[0].reflection_lesson == "Pool correlation foi decisiva"
        assert outcomes[0].tnr_passed is True

    def test_get_skill_outcomes_limit(self, tmp_path):
        """get_skill_outcomes deve respeitar o limite."""
        from rag.memory import IncidentMemoryDB, DiagnosticSkill, SkillOutcome

        db = IncidentMemoryDB(db_path=str(tmp_path / "test.db"))
        skill = DiagnosticSkill(
            skill_id="s-lim",
            name="test",
            scenario_pattern=".*",
            system_prompt="...",
            priority_tools=[],
        )
        db.save_skill(skill)
        for i in range(5):
            db.save_skill_outcome(SkillOutcome(
                outcome_id=f"o{i}",
                skill_id="s-lim",
                incident_id=f"i{i}",
                tnr_passed=True,
                reflection_score=0.7,
                reflection_lesson=f"lesson {i}",
                tools_used=[],
                iteration_count=2,
                timestamp="2026-03-26T00:00:00+00:00",
            ))
        outcomes = db.get_skill_outcomes("s-lim", limit=3)
        assert len(outcomes) == 3


# ---------------------------------------------------------------------------
# Fase 4: ROUTE — SkillRouter
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPhase4SkillRouter:
    """Fase 4: SkillRouter seleciona a melhor DiagnosticSkill para o incidente."""

    def test_router_returns_default_when_no_skills(self, tmp_path):
        """Sem skills no banco, SkillRouter retorna DEFAULT_SKILL."""
        from rag.memory import IncidentMemoryDB
        from agents.skill_router import SkillRouter, DEFAULT_SKILL

        db = IncidentMemoryDB(db_path=str(tmp_path / "test.db"))
        router = SkillRouter(db)
        skill = router.route(["unknown"], ["svc-x"])
        assert skill.name == DEFAULT_SKILL.name

    def test_router_returns_best_skill(self, tmp_path):
        """Router retorna skill com maior utility_score para o padrão dado."""
        from rag.memory import IncidentMemoryDB, DiagnosticSkill
        from agents.skill_router import SkillRouter

        db = IncidentMemoryDB(db_path=str(tmp_path / "test.db"))
        skill = DiagnosticSkill(
            skill_id="r1",
            name="pool-diag",
            scenario_pattern="connection pool",
            system_prompt="Specialized prompt...",
            priority_tools=["query_metrics"],
            utility_score=0.8,
        )
        db.save_skill(skill)
        router = SkillRouter(db)
        selected = router.route(["connection_pool"], ["payment-svc"])
        assert selected.name == "pool-diag"

    def test_router_ignores_low_score_skill(self, tmp_path):
        """Router ignora skills com utility_score < 0.3 e retorna DEFAULT_SKILL."""
        from rag.memory import IncidentMemoryDB, DiagnosticSkill
        from agents.skill_router import SkillRouter, DEFAULT_SKILL

        db = IncidentMemoryDB(db_path=str(tmp_path / "test.db"))
        skill = DiagnosticSkill(
            skill_id="r2",
            name="weak-diag",
            scenario_pattern="connection pool",
            system_prompt="...",
            priority_tools=[],
            utility_score=0.1,  # abaixo do threshold 0.3
        )
        db.save_skill(skill)
        router = SkillRouter(db)
        selected = router.route(["connection_pool"], ["payment-svc"])
        assert selected.name == DEFAULT_SKILL.name

    def test_diagnoser_accepts_skill_param(self):
        """DiagnosisAgent deve aceitar skill param no construtor."""
        from rag.memory import DiagnosticSkill

        skill = DiagnosticSkill(
            skill_id="d1",
            name="custom",
            scenario_pattern=".*",
            system_prompt="CUSTOM PROMPT FOR TESTING",
            priority_tools=[],
        )
        agent = DiagnosisAgent(skill=skill)
        assert agent.skill is not None
        assert agent.skill.system_prompt == "CUSTOM PROMPT FOR TESTING"

    def test_diagnoser_skill_none_by_default(self):
        """DiagnosisAgent sem skill deve ter self.skill = None."""
        agent = DiagnosisAgent()
        assert agent.skill is None

    def test_seed_default_skills_creates_3(self, tmp_path):
        """seed_default_skills deve criar 3 skills no banco."""
        from rag.memory import IncidentMemoryDB
        from agents.skill_router import SkillRouter

        db = IncidentMemoryDB(db_path=str(tmp_path / "test.db"))
        router = SkillRouter(db)
        router.seed_default_skills()
        with db._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM diagnostic_skills"
            ).fetchone()[0]
        assert count == 3


# ---------------------------------------------------------------------------
# Fase 5: EVOLVE — SkillEvolver
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPhase5SkillEvolver:
    """Fase 5: SkillEvolver decide quando e como evoluir uma skill."""

    def _make_skill(self, utility_score: float, total_uses: int):
        from rag.memory import DiagnosticSkill

        return DiagnosticSkill(
            skill_id=f"e-{utility_score}-{total_uses}",
            name="test",
            scenario_pattern=".*",
            system_prompt="...",
            priority_tools=[],
            utility_score=utility_score,
            total_uses=total_uses,
        )

    def test_should_evolve_false_not_enough_uses(self):
        """should_evolve retorna False quando total_uses < 3."""
        from agents.skill_evolver import SkillEvolver

        skill = self._make_skill(utility_score=0.1, total_uses=2)
        evolver = SkillEvolver(client=None, memory_db=None)
        assert not evolver.should_evolve(skill)

    def test_should_evolve_false_high_score(self):
        """should_evolve retorna False quando utility_score >= 0.3."""
        from agents.skill_evolver import SkillEvolver

        skill = self._make_skill(utility_score=0.5, total_uses=5)
        evolver = SkillEvolver(client=None, memory_db=None)
        assert not evolver.should_evolve(skill)

    def test_should_evolve_true_low_score_enough_uses(self):
        """should_evolve retorna True quando score < 0.3 e uses >= 3."""
        from agents.skill_evolver import SkillEvolver

        skill = self._make_skill(utility_score=0.2, total_uses=4)
        evolver = SkillEvolver(client=None, memory_db=None)
        assert evolver.should_evolve(skill)

    def test_should_evolve_boundary_exactly_3_uses(self):
        """should_evolve retorna True com exatamente 3 uses e score < 0.3."""
        from agents.skill_evolver import SkillEvolver

        skill = self._make_skill(utility_score=0.29, total_uses=3)
        evolver = SkillEvolver(client=None, memory_db=None)
        assert evolver.should_evolve(skill)

    def test_evolver_importable(self):
        """SkillEvolver e EVOLVE_PROMPT devem ser importáveis sem erro."""
        from agents.skill_evolver import SkillEvolver, EVOLVE_PROMPT

        assert EVOLVE_PROMPT  # não vazio
        assert "{skill_name}" in EVOLVE_PROMPT

    def test_evolver_init_accepts_none(self):
        """SkillEvolver(client=None, memory_db=None) não deve levantar exceção."""
        from agents.skill_evolver import SkillEvolver

        evolver = SkillEvolver(client=None, memory_db=None)
        assert evolver.client is None
        assert evolver.memory_db is None
