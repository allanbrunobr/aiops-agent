"""
CoT Retriever — rastreamento de contexto Chain-of-Thought por sessão de diagnóstico.

Estende ObservabilityRetriever para manter histórico de buscas e permitir
query expansion progressiva durante a investigação de incidentes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from rag.retriever import ObservabilityRetriever, SearchResult


@dataclass
class SearchContext:
    """Contexto de uma busca individual durante a sessão de diagnóstico."""

    iteration: int
    query: str
    results: list[SearchResult]
    entities: list[str]
    query_expansion: str | None = None


class CoTObservabilityRetriever(ObservabilityRetriever):
    """Retriever com rastreamento de contexto Chain-of-Thought por sessão de diagnóstico."""

    def __init__(self, indexer, reranker=None):
        super().__init__(indexer)
        self.reranker = reranker
        self.search_history: list[SearchContext] = []
        self.current_iteration: int = 0

    def reset_context(self) -> None:
        """Limpa histórico de buscas e reinicia contador de iterações."""
        self.search_history = []
        self.current_iteration = 0

    def search_with_cot(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Busca com rastreamento de contexto Chain-of-Thought."""
        from rag.query_expansion import QueryExpander

        self.current_iteration += 1

        # Expansão de query com base no histórico anterior
        expander = QueryExpander()
        expanded_query = expander.expand_query(query, self.search_history)

        # Realiza a busca — com reranker se disponível, caso contrário FAISS direto
        if self.reranker is not None:
            raw = self.search(expanded_query, top_k=top_k)
            results = self.reranker.rerank(query, raw)
        else:
            results = self.search(expanded_query, top_k=top_k)

        # Extrai entidades da query original
        entities = self._extract_entities(query)

        # Armazena contexto da iteração
        ctx = SearchContext(
            iteration=self.current_iteration,
            query=query,
            results=results,
            entities=entities,
            query_expansion=expanded_query if expanded_query != query else None,
        )
        self.search_history.append(ctx)

        return results

    def get_search_summary(self) -> str:
        """Retorna resumo formatado de todas as iterações de busca da sessão."""
        if not self.search_history:
            return "No searches performed yet."

        lines = ["=== Search History ==="]
        for ctx in self.search_history:
            top_entities = ", ".join(ctx.entities[:3]) if ctx.entities else "none"
            lines.append(
                f"Iteration {ctx.iteration}: query='{ctx.query}' "
                f"results={len(ctx.results)} entities=[{top_entities}]"
            )
        return "\n".join(lines)

    def _extract_entities(self, query: str) -> list[str]:
        """Extrai nomes de serviços e palavras-chave de métricas da query."""
        _metric_kws = {
            "connection_pool", "latency", "error_rate", "memory", "cpu",
            "throughput", "timeout", "heap", "gc", "connections",
        }
        entities: list[str] = []
        for word in query.split():
            clean = word.strip(".,;:()")
            if clean.endswith("-svc") or clean.endswith("-service"):
                entities.append(clean)
            elif clean.startswith("metric:"):
                entities.append(clean)
            elif clean in _metric_kws:
                entities.append(f"metric:{clean}")
        return entities
