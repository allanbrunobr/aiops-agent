"""
Skill Evolver — decide quando e como reescrever uma DiagnosticSkill fraca.

Fluxo:
  1. Após cada ciclo REFLECT+WRITE, chame should_evolve(skill)
  2. Se retornar True, chame evolve(skill) para gerar um novo system_prompt via LLM
  3. A skill evoluída é persistida com version+1 e score resetado para 0.5
"""

from __future__ import annotations

import json
import logging

from application.ports.llm_client import LLMClient
from rag.memory import DiagnosticSkill, IncidentMemoryDB

logger = logging.getLogger(__name__)

# Prompt enviado ao LLM para reescrever uma skill fraca.
EVOLVE_PROMPT = """\
You are a meta-agent that improves diagnostic skill prompts for SRE agents.

Skill name: {skill_name}
Current system prompt:
{system_prompt}

Recent failure lessons:
{lessons}

The skill has a low utility score ({utility_score:.2f}) after {total_uses} uses.
Rewrite the system prompt to improve diagnostic accuracy for this type of incident.

Return ONLY a JSON object with this structure:
{{"new_system_prompt": "...", "rationale": "..."}}
"""

# Limiar mínimo de utility_score para considerar uma skill candidata a evolução.
_EVOLVE_SCORE_THRESHOLD = 0.3
# Número mínimo de usos antes de considerar evolução (evita decisões prematuras).
_EVOLVE_MIN_USES = 3


class SkillEvolver:
    """Decide quando e como evoluir uma DiagnosticSkill com baixa performance."""

    def __init__(self, client: LLMClient | None, memory_db: IncidentMemoryDB | None) -> None:
        self.client = client
        self.memory_db = memory_db

    def should_evolve(self, skill: DiagnosticSkill) -> bool:
        """Retorna True se a skill deve ser reescrita.

        Critérios:
        - total_uses >= _EVOLVE_MIN_USES  (evita decisões com dados insuficientes)
        - utility_score < _EVOLVE_SCORE_THRESHOLD  (performance abaixo do mínimo)
        """
        return (
            skill.total_uses >= _EVOLVE_MIN_USES
            and skill.utility_score < _EVOLVE_SCORE_THRESHOLD
        )

    def evolve(self, skill: DiagnosticSkill) -> DiagnosticSkill | None:
        """Reescreve o system_prompt da skill via LLM.

        Retorna a skill atualizada (com version+1) ou None se o LLM não estiver
        disponível ou a chamada falhar.
        """
        if self.client is None:
            logger.warning("SkillEvolver: cliente LLM ausente — evolução ignorada.")
            return None

        lessons = self._fetch_recent_lessons(skill.skill_id)
        prompt = EVOLVE_PROMPT.format(
            skill_name=skill.name,
            system_prompt=skill.system_prompt,
            lessons="\n".join(f"- {l}" for l in lessons) or "(nenhuma lição registrada)",
            utility_score=skill.utility_score,
            total_uses=skill.total_uses,
        )

        try:
            response = self.client.create_message(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            data = json.loads(raw)
            new_prompt = data.get("new_system_prompt", "").strip()
            if not new_prompt:
                return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("SkillEvolver.evolve falhou: %s", exc)
            return None

        evolved = DiagnosticSkill(
            skill_id=skill.skill_id,
            name=skill.name,
            scenario_pattern=skill.scenario_pattern,
            system_prompt=new_prompt,
            priority_tools=skill.priority_tools,
            utility_score=0.5,  # reseta o score para dar nova chance à skill
            total_uses=skill.total_uses,
            success_count=skill.success_count,
            last_reflection=skill.last_reflection,
            version=skill.version + 1,
        )

        if self.memory_db is not None:
            self.memory_db.save_skill(evolved)

        return evolved

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _fetch_recent_lessons(self, skill_id: str, limit: int = 5) -> list[str]:
        """Busca as lições mais recentes registradas para a skill."""
        if self.memory_db is None:
            return []
        outcomes = self.memory_db.get_skill_outcomes(skill_id, limit=limit)
        return [o.reflection_lesson for o in outcomes if o.reflection_lesson]
