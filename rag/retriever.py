"""
Retriever semântico para busca em dados de observabilidade indexados.
"""

from __future__ import annotations

import numpy as np
from rag.indexer import ObservabilityIndexer, IndexedChunk


class ObservabilityRetriever:
    """Busca semântica em dados de observabilidade."""

    def __init__(self, indexer: ObservabilityIndexer):
        self.indexer = indexer

    def search(self, query: str, top_k: int = 10, source_filter: str | None = None) -> list[SearchResult]:
        """Busca os chunks mais relevantes para a query."""
        if self.indexer.index is None or self.indexer.model is None:
            return []

        query_embedding = self.indexer.model.encode([query], normalize_embeddings=True)
        query_embedding = np.array(query_embedding, dtype=np.float32)

        # Busca mais do que top_k para compensar filtragem
        search_k = top_k * 3 if source_filter else top_k
        distances, indices = self.indexer.index.search(query_embedding, min(search_k, len(self.indexer.chunks)))

        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0:
                continue
            chunk = self.indexer.chunks[idx]
            if source_filter and chunk.source != source_filter:
                continue
            results.append(SearchResult(
                chunk=chunk,
                score=float(dist),
            ))
            if len(results) >= top_k:
                break

        return results

    def search_logs(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Busca apenas em log templates."""
        return self.search(query, top_k=top_k, source_filter="log")

    def search_metrics(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Busca apenas em anomalias de métricas."""
        return self.search(query, top_k=top_k, source_filter="metric")

    def search_traces(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Busca apenas em traces lentos."""
        return self.search(query, top_k=top_k, source_filter="trace")

    def format_results(self, results: list[SearchResult]) -> str:
        """Formata resultados para o LLM."""
        if not results:
            return "Nenhum resultado encontrado."

        lines = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. [{r.chunk.source.upper()}] (score={r.score:.3f}) {r.chunk.text}")
        return "\n".join(lines)


class SearchResult:
    """Resultado de uma busca semântica."""

    def __init__(self, chunk: IndexedChunk, score: float):
        self.chunk = chunk
        self.score = score

    def __repr__(self):
        return f"SearchResult(source={self.chunk.source}, score={self.score:.3f})"
