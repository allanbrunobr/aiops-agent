from __future__ import annotations

import logging
import time

import numpy as np

from rag.models import KnowledgePattern, KnowledgeSearchResult

logger = logging.getLogger(__name__)


class KnowledgeRetriever:
    """Busca semântica na base de conhecimento de incidentes."""

    def __init__(self, indexer: object, patterns: list[KnowledgePattern]) -> None:
        """Inicializa retriever com indexer e padrões."""
        self.indexer = indexer
        self.patterns = patterns

    def search_similar_incidents(
        self,
        query: str,
        top_k: int = 5,
    ) -> list[KnowledgeSearchResult]:
        """Busca incidentes similares na base de conhecimento.

        Filtra negative_examples e retorna top-K por similaridade cosseno.
        Retorna lista vazia graciosamente se índice ou modelo não disponível.
        """
        if not hasattr(self.indexer, "index") or self.indexer.index is None:
            logger.warning("rag.unavailable", extra={"reason": "index_missing"})
            return []

        if not hasattr(self.indexer, "model") or self.indexer.model is None:
            logger.warning("rag.unavailable", extra={"reason": "model_not_loaded"})
            return []

        if not self.patterns:
            return []

        start = time.monotonic()

        try:
            query_embedding = self.indexer.model.encode(
                [query], normalize_embeddings=True
            )
            query_embedding = np.array(query_embedding, dtype=np.float32)

            # Busca mais que top_k para compensar filtragem de negative_examples
            search_k = min(top_k * 3, len(self.patterns))
            if search_k == 0:
                return []

            distances, indices = self.indexer.index.search(query_embedding, search_k)

            results = []
            for dist, idx in zip(distances[0], indices[0]):
                if idx < 0 or idx >= len(self.patterns):
                    continue
                pattern = self.patterns[idx]
                if pattern.is_negative_example:
                    continue
                results.append(KnowledgeSearchResult(
                    pattern=pattern,
                    similarity_score=float(dist),
                ))
                if len(results) >= top_k:
                    break

        except Exception as exc:
            logger.warning(
                "rag.unavailable",
                extra={"reason": "search_error", "error": str(exc)},
            )
            return []

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.info(
            "rag.search_complete",
            extra={
                "results": len(results),
                "latency_ms": round(elapsed_ms, 1),
                "top_k": top_k,
            },
        )

        return results
