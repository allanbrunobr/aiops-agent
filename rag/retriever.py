"""
Retriever semântico para busca em dados de observabilidade indexados.
"""

from __future__ import annotations

import hashlib
import logging
import warnings

import numpy as np
from rag.indexer import ObservabilityIndexer, IndexedChunk

_query_cache: dict[str, np.ndarray] = {}

logger = logging.getLogger(__name__)


class ObservabilityRetriever:
    """Busca semântica em dados de observabilidade.

    Suporta reranking opcional via DomainReranker para refinar os candidatos
    retornados pelo FAISS usando sinais de domínio. A interface sem reranker
    mantém comportamento idêntico à versão V1 (backward compatible).
    """

    def __init__(
        self,
        indexer: ObservabilityIndexer,
        reranker: object | None = None,
    ) -> None:
        """Inicializa o retriever com indexer obrigatório e reranker opcional.

        Args:
            indexer: Instância de ObservabilityIndexer com índice FAISS construído.
            reranker: Instância de DomainReranker opcional. Se None, comportamento
                V1 (ordenação por score FAISS direto) é preservado.
        """
        self.indexer = indexer
        self.reranker = reranker

    def search(
        self,
        query: str,
        top_k: int = 10,
        source_filter: str | None = None,
        use_reranking: bool = True,
    ) -> list[SearchResult]:
        """Busca os chunks mais relevantes para a query.

        Se reranker estiver configurado e use_reranking=True, recupera top_k*2
        candidatos do FAISS e aplica reranking para refinar os resultados.
        Em caso de falha no reranking, retorna os resultados FAISS originais.

        Se reranker for None ou use_reranking=False, comportamento V1 é preservado.

        Args:
            query: Texto de consulta para busca semântica.
            top_k: Número máximo de resultados a retornar.
            source_filter: Filtro opcional por tipo de fonte ("log", "metric", "trace").
            use_reranking: Se False, ignora o reranker mesmo que esteja configurado.

        Returns:
            Lista de até top_k SearchResult ordenados por relevância decrescente.
        """
        if self.indexer.index is None or self.indexer.model is None:
            return []

        # Determina se reranking está ativo nesta chamada
        reranking_active = (
            self.reranker is not None
            and use_reranking
            and source_filter is None  # reranking não combinado com source_filter
        )

        key = hashlib.md5(query.encode()).hexdigest()
        if key not in _query_cache:
            _query_cache[key] = np.array(
                self.indexer.model.encode([query], normalize_embeddings=True),
                dtype=np.float32,
            )
        query_embedding = _query_cache[key]

        if reranking_active:
            # Recupera top_k*2 candidatos para o reranker refinar
            faiss_k = top_k * 2
        else:
            # Busca mais do que top_k para compensar filtragem
            faiss_k = top_k * 3 if source_filter else top_k

        # Índice FAISS contém apenas embeddings únicos; mapeia de volta para chunks
        faiss_to_chunks = getattr(self.indexer, "_faiss_to_chunks", None)
        n_faiss = self.indexer.index.ntotal
        distances, indices = self.indexer.index.search(
            query_embedding,
            min(faiss_k, n_faiss),
        )

        results = []
        limit = faiss_k if reranking_active else top_k
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0:
                continue
            # Resolve FAISS idx → chunk(s)
            if faiss_to_chunks is not None:
                chunk_indices = faiss_to_chunks[idx]
                chunk = next(
                    (self.indexer.chunks[ci] for ci in chunk_indices
                     if not source_filter or self.indexer.chunks[ci].source == source_filter),
                    None,
                )
            else:
                chunk = self.indexer.chunks[idx]
                if source_filter and chunk.source != source_filter:
                    chunk = None
            if chunk is None:
                continue
            results.append(SearchResult(
                chunk=chunk,
                score=float(dist),
            ))
            if len(results) >= limit:
                break

        # Aplica reranking se ativo
        if reranking_active and results:
            try:
                results = self.reranker.rerank(results, query, top_k=top_k)  # type: ignore[attr-defined]
            except Exception as exc:
                warnings.warn(
                    f"Reranker falhou — retornando resultados FAISS brutos: {exc}",
                    RuntimeWarning,
                    stacklevel=2,
                )
                logger.warning("Reranker exception: %s", exc)
                results = results[:top_k]

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
