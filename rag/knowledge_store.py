from __future__ import annotations

import json
import logging
import os

from rag.models import KnowledgePattern

logger = logging.getLogger(__name__)


class KnowledgeStore:
    """Armazenamento de padrões de incidentes com persistência JSON.

    V1: in-memory + arquivo JSON. V2: SQLite via mesmo Protocol.
    """

    def __init__(self, storage_path: str = "data/knowledge/patterns.json") -> None:
        """Inicializa store com caminho de armazenamento."""
        self._patterns: list[KnowledgePattern] = []
        self._storage_path = storage_path
        self._index_stale = False

    def add_pattern(self, pattern: KnowledgePattern) -> None:
        """Adiciona padrão e marca índice como stale."""
        self._patterns.append(pattern)
        self._index_stale = True
        self._persist()
        logger.info(
            "knowledge.stored",
            extra={
                "pattern_id": pattern.pattern_id,
                "total_patterns": len(self._patterns),
            },
        )

    def find_by_service_and_cause(
        self, service: str, root_cause: str,
    ) -> KnowledgePattern | None:
        """Busca padrão existente com mesmo serviço e causa raiz."""
        for p in self._patterns:
            if p.service == service and p.root_cause == root_cause:
                return p
        return None

    def get_all_patterns(self) -> list[KnowledgePattern]:
        """Retorna todos os padrões (incluindo negative_examples)."""
        return list(self._patterns)

    @property
    def is_index_stale(self) -> bool:
        """Indica se o índice FAISS precisa ser reconstruído."""
        return self._index_stale

    def mark_index_rebuilt(self) -> None:
        """Marca índice como atualizado após rebuild."""
        self._index_stale = False

    def mark_pattern_negative(self, pattern_id: str) -> bool:
        """Marca padrão como negative_example. Retorna True se encontrado."""
        for p in self._patterns:
            if p.pattern_id == pattern_id:
                p.is_negative_example = True
                self._index_stale = True
                self._persist()
                logger.info(
                    "knowledge.pattern_marked_negative",
                    extra={"pattern_id": pattern_id},
                )
                return True
        return False

    def _persist(self) -> None:
        """Persiste padrões em arquivo JSON."""
        if not self._storage_path:
            return
        try:
            os.makedirs(os.path.dirname(self._storage_path), exist_ok=True)
        except (OSError, ValueError):
            return
        data = [
            {
                "pattern_id": p.pattern_id,
                "service": p.service,
                "symptoms": p.symptoms,
                "root_cause": p.root_cause,
                "resolution": p.resolution,
                "tags": p.tags,
                "confidence": p.confidence,
                "is_negative_example": p.is_negative_example,
                "created_at": p.created_at,
            }
            for p in self._patterns
        ]
        with open(self._storage_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_from_disk(self) -> int:
        """Carrega padrões de arquivo JSON. Retorna número de padrões carregados."""
        if not os.path.isfile(self._storage_path):
            return 0
        try:
            with open(self._storage_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._patterns = [KnowledgePattern(**item) for item in data]
            return len(self._patterns)
        except Exception as exc:
            logger.warning(
                "knowledge.load_failed",
                extra={"error": str(exc), "path": self._storage_path},
            )
            return 0
