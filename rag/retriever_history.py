"""
Retriever semântico com suporte a precedentes históricos de incidentes.

Extende ObservabilityRetriever com capacidade de consultar o banco de
memória institucional (IncidentMemoryDB) para exibir incidentes similares
antes do diagnóstico (Epic 7 — Institutional Memory).
"""

from __future__ import annotations

from rag.retriever import ObservabilityRetriever
from rag.indexer import ObservabilityIndexer


class ObservabilityRetrieverWithHistory(ObservabilityRetriever):
    """Retriever com suporte a precedentes históricos de incidentes."""

    def __init__(
        self,
        indexer: ObservabilityIndexer,
        db_path: str = "data/incident_memory.db",
    ) -> None:
        super().__init__(indexer)
        self.db_path = db_path

    def get_similar_past_incidents(
        self,
        services: list[str],
        tags: list[str],
        limit: int = 3,
    ) -> list[dict]:
        """Busca precedentes históricos similares ao incidente atual.

        Consulta o IncidentMemoryDB e retorna até `limit` incidentes
        formatados para exibição no console antes do diagnóstico.

        Args:
            services: Serviços afetados no incidente atual.
            tags: Tags de falha heurísticas do incidente atual.
            limit: Número máximo de precedentes a retornar.

        Returns:
            Lista de dicts com campos: scenario, root_cause,
            affected_services, resolution_time_sec, diagnosis_confidence.
            Retorna lista vazia se não houver precedentes ou em caso de erro.
        """
        try:
            from rag.memory import IncidentMemoryDB

            db = IncidentMemoryDB(db_path=self.db_path)
            records = db.find_similar_incidents(services, tags, limit=limit)

            return [
                {
                    "scenario": r.scenario,
                    "root_cause": r.root_cause,
                    "affected_services": r.affected_services,
                    "resolution_time_sec": r.resolution_time_sec,
                    "diagnosis_confidence": r.diagnosis_confidence,
                }
                for r in records
            ]
        except Exception:
            return []
