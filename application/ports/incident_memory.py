"""Porta da memória institucional (incidentes passados e skills de diagnóstico).

A implementação é rag.memory.IncidentMemoryDB (SQLite). Os agentes dependem
desta porta, não da classe concreta (A4/ARQ-16). Os tipos de valor
(IncidentRecord, DiagnosticSkill, SkillOutcome) vivem em rag.memory; aqui
ficam como Any para application/ continuar importando só domain/.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IncidentMemory(Protocol):
    """Operações de memória usadas pelos agentes e pela composição."""

    def save_incident(self, record: Any) -> None: ...

    def find_similar_incidents(
        self, affected_services: list[str], tags: list[str], limit: int = 5,
    ) -> list[Any]: ...

    def get_skill(self, skill_id: str) -> Any | None: ...

    def save_skill(self, skill: Any) -> None: ...

    def find_best_skill(self, tags: list[str], services: list[str]) -> Any | None: ...

    def save_skill_outcome(self, outcome: Any) -> None: ...

    def get_skill_outcomes(self, skill_id: str, limit: int = 10) -> list[Any]: ...

    def update_skill_score(
        self, skill_id: str, tnr_passed: bool, reflection_score: float,
    ) -> None: ...

    def save_skill_proposal(
        self, skill_id: str, base_version: int, proposed_prompt: str, rationale: str,
    ) -> str: ...
