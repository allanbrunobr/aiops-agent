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
