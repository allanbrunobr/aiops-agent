"""
Query Expander — expansão progressiva de queries de busca RAG.

Evita re-investigação de entidades já cobertas e sugere follow-up
baseado em padrões encontrados nos resultados anteriores.
"""

from __future__ import annotations

from rag.cot_retriever import SearchContext


class QueryExpander:
    """Expande queries de busca com base no histórico de investigação."""

    def expand_query(self, query: str, history: list[SearchContext]) -> str:
        """Expande query com contexto de entidades já investigadas."""
        if not history:
            return query

        # Coleta entidades únicas de todas as iterações anteriores
        seen: set[str] = set()
        all_entities: list[str] = []
        for ctx in history:
            for entity in ctx.entities:
                if entity not in seen:
                    all_entities.append(entity)
                    seen.add(entity)

        parts = [query]

        if all_entities:
            exclusion = ", ".join(all_entities)
            parts.append(f"(excluding already investigated: {exclusion})")

        # Sugestão de follow-up a partir da última iteração
        follow_up = self._generate_follow_up_query(history[-1])
        if follow_up:
            parts.append(f"(follow-up: {follow_up})")

        if len(parts) == 1:
            return query

        return " ".join(parts)

    def _generate_follow_up_query(self, prev_context: SearchContext) -> str | None:
        """Gera sugestão de follow-up baseada nos resultados da iteração anterior."""
        if not prev_context.results:
            return None

        top_text = prev_context.results[0].chunk.text.lower()

        if "connection pool" in top_text:
            return "connection pool configuration and limits"
        if "memory" in top_text:
            return "garbage collection and heap memory"

        return None
