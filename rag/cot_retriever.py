"""
CoT Retriever — rastreamento de contexto Chain-of-Thought por sessão de diagnóstico.

Estende ObservabilityRetriever para manter histórico de buscas e permitir
query expansion progressiva durante a investigação de incidentes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

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
        # O reranker vai para a base: ObservabilityRetriever.search() já aplica
        # rerank(results, query, top_k=) com fallback seguro. F6-2: antes esta
        # classe guardava o reranker e chamava rerank(query, raw) — argumentos
        # invertidos → AttributeError ao ativar — e ainda por cima DEPOIS de
        # search() já ter reranqueado (duplo rerank).
        super().__init__(indexer, reranker=reranker)
        # Correção pós-revisão #4: estado de expansão POR-RUN (thread-local).
        # O retriever é montado uma vez em create_app e compartilhado por runs
        # A2A/MCP concorrentes (asyncio.to_thread(engine.run, ...)); estado de
        # instância contaminava o histórico entre runs. Cada thread de run tem
        # seu próprio histórico; reset_context() limpa o do thread corrente.
        self._local = threading.local()

    @property
    def search_history(self) -> list[SearchContext]:
        """Histórico de buscas do run corrente (thread-local)."""
        hist = getattr(self._local, "search_history", None)
        if hist is None:
            hist = []
            self._local.search_history = hist
        return hist

    @search_history.setter
    def search_history(self, value: list[SearchContext]) -> None:
        self._local.search_history = value

    @property
    def current_iteration(self) -> int:
        """Contador de iterações do run corrente (thread-local)."""
        return getattr(self._local, "current_iteration", 0)

    @current_iteration.setter
    def current_iteration(self, value: int) -> None:
        self._local.current_iteration = value

    def reset_context(self) -> None:
        """Limpa histórico e contador do run corrente (não afeta outros runs)."""
        self._local.search_history = []
        self._local.current_iteration = 0

    def search_with_cot(self, query: str, top_k: int = 5) -> list[SearchResult]:
        """Busca com rastreamento de contexto Chain-of-Thought."""
        from rag.query_expansion import QueryExpander

        self.current_iteration += 1

        # Expansão de query com base no histórico anterior
        expander = QueryExpander()
        expanded_query = expander.expand_query(query, self.search_history)

        # F5-12: pede à base os resultados BRUTOS (use_reranking=False) e
        # reranqueia UMA vez aqui, com a assinatura correta rerank(results,
        # query, top_k) e a query ORIGINAL (não a expandida). Com index vazio a
        # base retorna [] e o rerank é pulado — evita duplo rerank e a
        # assinatura invertida rerank(query, raw) do bug P3-9.
        results = self.search(expanded_query, top_k=top_k, use_reranking=False)
        if self.reranker is not None and results:
            results = self.reranker.rerank(results, query, top_k=top_k)

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
            "connection_pool",
            "latency",
            "error_rate",
            "memory",
            "cpu",
            "throughput",
            "timeout",
            "heap",
            "gc",
            "connections",
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
