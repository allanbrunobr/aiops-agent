"""
Banco de dados SQLite para memória institucional de incidentes.

Persiste diagnósticos concluídos e permite recuperação de precedentes
relevantes para futuros diagnósticos (Epic 7 — Institutional Memory).
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field


@dataclass
class IncidentRecord:
    """Registro de um incidente diagnosticado."""

    incident_id: str
    timestamp: str          # ISO 8601
    scenario: str
    root_cause: str
    affected_services: list[str]
    diagnosis_confidence: float
    resolution_time_sec: float
    tags: list[str]
    evidence_summary: str = ""

    @classmethod
    def new(
        cls,
        scenario: str,
        root_cause: str,
        affected_services: list[str],
        diagnosis_confidence: float,
        resolution_time_sec: float,
        tags: list[str],
        evidence_summary: str = "",
    ) -> IncidentRecord:
        """Cria novo registro com incident_id e timestamp gerados automaticamente."""
        import datetime

        return cls(
            incident_id=str(uuid.uuid4()),
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            scenario=scenario,
            root_cause=root_cause,
            affected_services=affected_services,
            diagnosis_confidence=diagnosis_confidence,
            resolution_time_sec=resolution_time_sec,
            tags=tags,
            evidence_summary=evidence_summary,
        )


_DDL = """
CREATE TABLE IF NOT EXISTS incidents (
    incident_id         TEXT PRIMARY KEY,
    timestamp           TEXT NOT NULL,
    scenario            TEXT NOT NULL,
    root_cause          TEXT NOT NULL,
    affected_services   TEXT NOT NULL,
    diagnosis_confidence REAL NOT NULL,
    resolution_time_sec  REAL NOT NULL,
    tags                TEXT NOT NULL,
    evidence_summary    TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_incidents_scenario
    ON incidents (scenario);

CREATE INDEX IF NOT EXISTS idx_incidents_timestamp
    ON incidents (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_incidents_services
    ON incidents (affected_services);
"""


@dataclass
class DiagnosticSkill:
    """Skill de diagnóstico que evolui com a experiência acumulada."""

    skill_id: str
    name: str
    scenario_pattern: str          # ex: "connection pool|pool exhausted"
    system_prompt: str             # prompt especializado para este tipo de incidente
    priority_tools: list[str]      # ex: ["correlate_signals", "query_metrics"]
    utility_score: float = 0.5    # 0.0–1.0, começa neutro
    total_uses: int = 0
    success_count: int = 0
    last_reflection: str = ""     # lição mais recente gerada pelo LLM
    version: int = 1
    created_at: str = ""          # ISO 8601
    updated_at: str = ""          # ISO 8601


@dataclass
class SkillOutcome:
    """Registro de uma execução de skill com seu resultado."""

    outcome_id: str
    skill_id: str
    incident_id: str
    tnr_passed: bool
    reflection_score: float
    reflection_lesson: str
    tools_used: list[str]
    iteration_count: int
    timestamp: str                 # ISO 8601


_DDL_SKILLS = """
CREATE TABLE IF NOT EXISTS diagnostic_skills (
    skill_id            TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    scenario_pattern    TEXT NOT NULL,
    system_prompt       TEXT NOT NULL,
    priority_tools      TEXT NOT NULL,
    utility_score       REAL NOT NULL DEFAULT 0.5,
    total_uses          INTEGER NOT NULL DEFAULT 0,
    success_count       INTEGER NOT NULL DEFAULT 0,
    last_reflection     TEXT NOT NULL DEFAULT '',
    version             INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT '',
    updated_at          TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_skills_scenario
    ON diagnostic_skills (scenario_pattern);

CREATE INDEX IF NOT EXISTS idx_skills_score
    ON diagnostic_skills (utility_score DESC);
"""

_DDL_SKILL_OUTCOMES = """
CREATE TABLE IF NOT EXISTS skill_outcomes (
    outcome_id          TEXT PRIMARY KEY,
    skill_id            TEXT NOT NULL,
    incident_id         TEXT NOT NULL,
    tnr_passed          INTEGER NOT NULL,
    reflection_score    REAL NOT NULL,
    reflection_lesson   TEXT NOT NULL,
    tools_used          TEXT NOT NULL,
    iteration_count     INTEGER NOT NULL,
    timestamp           TEXT NOT NULL,
    FOREIGN KEY (skill_id) REFERENCES diagnostic_skills(skill_id)
);

CREATE INDEX IF NOT EXISTS idx_outcomes_skill
    ON skill_outcomes (skill_id, timestamp DESC);
"""


_DDL_SIGNATURES = """
CREATE TABLE IF NOT EXISTS degradation_signatures (
    incident_id   TEXT PRIMARY KEY,
    risk_score    REAL NOT NULL,
    signal_names  TEXT NOT NULL,
    text_repr     TEXT NOT NULL,
    timestamp     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signatures_timestamp
    ON degradation_signatures (timestamp DESC);
"""


class IncidentMemoryDB:
    """Banco de dados SQLite para memória de incidentes."""

    def __init__(self, db_path: str = "data/incident_memory.db") -> None:
        """Inicializa o banco de dados, criando esquema se necessário.

        Args:
            db_path: Caminho para o arquivo SQLite. Criado automaticamente.
        """
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path) if os.path.dirname(db_path) else ".", exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_DDL)
            conn.executescript(_DDL_SKILLS)
            conn.executescript(_DDL_SKILL_OUTCOMES)

    def _connect(self) -> sqlite3.Connection:
        """Abre conexão SQLite com suporte a chaves estrangeiras."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save_incident(self, record: IncidentRecord) -> None:
        """Persiste ou atualiza um registro de incidente (upsert).

        Usa INSERT OR REPLACE para garantir idempotência por incident_id.

        Args:
            record: IncidentRecord com todos os campos preenchidos.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO incidents
                    (incident_id, timestamp, scenario, root_cause,
                     affected_services, diagnosis_confidence, resolution_time_sec,
                     tags, evidence_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.incident_id,
                    record.timestamp,
                    record.scenario,
                    record.root_cause,
                    json.dumps(record.affected_services),
                    record.diagnosis_confidence,
                    record.resolution_time_sec,
                    json.dumps(record.tags),
                    record.evidence_summary,
                ),
            )

    def find_similar_incidents(
        self,
        affected_services: list[str],
        tags: list[str],
        limit: int = 5,
    ) -> list[IncidentRecord]:
        """Busca incidentes similares por sobreposição de serviços e tags.

        Pontuação: overlap de serviços (peso 2) + overlap de tags (peso 1).
        Ordena de forma decrescente e retorna até `limit` registros.

        Args:
            affected_services: Serviços afetados no incidente atual.
            tags: Tags de falha do incidente atual.
            limit: Número máximo de resultados.

        Returns:
            Lista de IncidentRecord ordenada por similaridade descendente.
        """
        if not affected_services and not tags:
            return []

        services_set = set(s.lower() for s in affected_services)
        tags_set = set(t.lower() for t in tags)

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM incidents ORDER BY timestamp DESC"
            ).fetchall()

        scored: list[tuple[int, IncidentRecord]] = []
        for row in rows:
            rec_services = set(s.lower() for s in json.loads(row["affected_services"]))
            rec_tags = set(t.lower() for t in json.loads(row["tags"]))

            service_overlap = len(services_set & rec_services) * 2
            tag_overlap = len(tags_set & rec_tags)
            score = service_overlap + tag_overlap

            if score > 0:
                record = IncidentRecord(
                    incident_id=row["incident_id"],
                    timestamp=row["timestamp"],
                    scenario=row["scenario"],
                    root_cause=row["root_cause"],
                    affected_services=json.loads(row["affected_services"]),
                    diagnosis_confidence=row["diagnosis_confidence"],
                    resolution_time_sec=row["resolution_time_sec"],
                    tags=json.loads(row["tags"]),
                    evidence_summary=row["evidence_summary"],
                )
                scored.append((score, record))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:limit]]

    # --- Skill Evolution: DiagnosticSkill CRUD ---

    def save_skill(self, skill: DiagnosticSkill) -> None:
        """Persiste ou atualiza uma DiagnosticSkill (upsert por skill_id)."""
        import datetime

        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        created_at = skill.created_at or now
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO diagnostic_skills
                    (skill_id, name, scenario_pattern, system_prompt, priority_tools,
                     utility_score, total_uses, success_count, last_reflection,
                     version, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    skill.skill_id,
                    skill.name,
                    skill.scenario_pattern,
                    skill.system_prompt,
                    json.dumps(skill.priority_tools),
                    skill.utility_score,
                    skill.total_uses,
                    skill.success_count,
                    skill.last_reflection,
                    skill.version,
                    created_at,
                    now,
                ),
            )

    def get_skill(self, skill_id: str) -> DiagnosticSkill | None:
        """Busca skill por ID. Retorna None se não encontrada."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM diagnostic_skills WHERE skill_id = ?", (skill_id,)
            ).fetchone()
        return self._row_to_skill(row) if row else None

    def find_best_skill(
        self,
        tags: list[str],
        services: list[str],
    ) -> DiagnosticSkill | None:
        """Busca skill com maior utility_score cujo scenario_pattern sobreponha as tags."""
        if not tags and not services:
            return None

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM diagnostic_skills ORDER BY utility_score DESC"
            ).fetchall()

        search_text = " ".join(tags + services).lower()
        for row in rows:
            pattern = row["scenario_pattern"].lower()
            # Verifica se qualquer token do padrão aparece no texto de busca
            tokens = [t.strip() for t in pattern.replace("|", " ").split()]
            if any(tok in search_text for tok in tokens if tok not in (".*", ".")):
                return self._row_to_skill(row)
        return None

    def update_skill_score(
        self,
        skill_id: str,
        tnr_passed: bool,
        reflection_score: float,
    ) -> None:
        """Atualiza utility_score via EMA (α=0.3) e incrementa contadores."""
        import datetime

        skill = self.get_skill(skill_id)
        if skill is None:
            return

        alpha = 0.3
        new_score = alpha * reflection_score + (1 - alpha) * skill.utility_score
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE diagnostic_skills
                SET utility_score = ?,
                    total_uses = total_uses + 1,
                    success_count = success_count + ?,
                    updated_at = ?
                WHERE skill_id = ?
                """,
                (new_score, 1 if tnr_passed else 0, now, skill_id),
            )

    def save_skill_outcome(self, outcome: SkillOutcome) -> None:
        """Registra o resultado de uma execução de skill."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO skill_outcomes
                    (outcome_id, skill_id, incident_id, tnr_passed,
                     reflection_score, reflection_lesson, tools_used,
                     iteration_count, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    outcome.outcome_id,
                    outcome.skill_id,
                    outcome.incident_id,
                    1 if outcome.tnr_passed else 0,
                    outcome.reflection_score,
                    outcome.reflection_lesson,
                    json.dumps(outcome.tools_used),
                    outcome.iteration_count,
                    outcome.timestamp,
                ),
            )

    def get_skill_outcomes(
        self,
        skill_id: str,
        limit: int = 5,
    ) -> list[SkillOutcome]:
        """Retorna os outcomes mais recentes de uma skill."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM skill_outcomes
                WHERE skill_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (skill_id, limit),
            ).fetchall()
        return [self._row_to_outcome(r) for r in rows]

    @staticmethod
    def _row_to_skill(row) -> DiagnosticSkill:
        return DiagnosticSkill(
            skill_id=row["skill_id"],
            name=row["name"],
            scenario_pattern=row["scenario_pattern"],
            system_prompt=row["system_prompt"],
            priority_tools=json.loads(row["priority_tools"]),
            utility_score=row["utility_score"],
            total_uses=row["total_uses"],
            success_count=row["success_count"],
            last_reflection=row["last_reflection"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_to_outcome(row) -> SkillOutcome:
        return SkillOutcome(
            outcome_id=row["outcome_id"],
            skill_id=row["skill_id"],
            incident_id=row["incident_id"],
            tnr_passed=bool(row["tnr_passed"]),
            reflection_score=row["reflection_score"],
            reflection_lesson=row["reflection_lesson"],
            tools_used=json.loads(row["tools_used"]),
            iteration_count=row["iteration_count"],
            timestamp=row["timestamp"],
        )

    # --- Métodos de assinatura pré-incidente (Story 2.1 / Story 2.2) ---

    def _ensure_signatures_table(self) -> None:
        """Garante que a tabela degradation_signatures existe no banco."""
        with self._connect() as conn:
            conn.executescript(_DDL_SIGNATURES)

    def store_degradation_signature(
        self,
        signals: list,
        risk_score: float,
        incident_id: str,
    ) -> None:
        """Persiste assinatura de degradação pré-incidente no banco de dados.

        Armazena somente se risk_score >= 30 (limiar de relevância preditiva).
        Usa INSERT OR REPLACE para garantir idempotência por incident_id.

        Args:
            signals: Lista de sinais de degradação detectados.
            risk_score: Score de risco calculado pelo predictive stage (0–100).
            incident_id: Identificador único do incidente relacionado.
        """
        if risk_score < 30:
            return

        import datetime

        self._ensure_signatures_table()
        signal_names = [s.__class__.__name__ for s in signals]
        text_repr = (
            f"pre_incident:{incident_id} risk:{risk_score:.1f} signals:{signal_names}"
        )
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO degradation_signatures
                    (incident_id, risk_score, signal_names, text_repr, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (incident_id, risk_score, json.dumps(signal_names), text_repr, now),
            )

    def query_similar_signatures(
        self,
        signals: list,
        top_k: int = 3,
    ) -> list[dict]:
        """Busca assinaturas de degradação históricas similares aos sinais atuais.

        Calcula similaridade de Jaccard entre os nomes de classe dos sinais
        atuais e os armazenados. Retorna os top_k registros mais similares.

        Args:
            signals: Lista de sinais de degradação atuais.
            top_k: Número máximo de resultados a retornar.

        Returns:
            Lista de dicts com 'incident_id', 'similarity_score' e 'outcome'.
            Retorna [] em caso de exceção ou base de assinaturas vazia.
        """
        try:
            self._ensure_signatures_table()
            query_names = set(s.__class__.__name__ for s in signals)

            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM degradation_signatures ORDER BY timestamp DESC"
                ).fetchall()

            if not rows:
                return []

            scored: list[tuple[float, dict]] = []
            for row in rows:
                stored_names = set(json.loads(row["signal_names"]))
                union = query_names | stored_names
                intersection = query_names & stored_names
                similarity = len(intersection) / len(union) if union else 0.0

                scored.append((similarity, {
                    "incident_id": row["incident_id"],
                    "similarity_score": round(similarity, 3),
                    "outcome": f"pre_incident risk={row['risk_score']:.1f}",
                }))

            scored.sort(key=lambda x: x[0], reverse=True)
            return [d for _, d in scored[:top_k]]
        except Exception:
            return []
