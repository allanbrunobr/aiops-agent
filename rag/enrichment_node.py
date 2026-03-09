from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class RAGEnrichmentNode:
    """Nó de enriquecimento RAG com degradação graciosa.

    Consulta a base de conhecimento e anexa contexto histórico
    ao pipeline para consumo pelo nó de raciocínio LLM.
    Se o índice FAISS estiver indisponível, o pipeline continua
    sem enriquecimento RAG. Nunca propaga exceções.
    """

    def __init__(self, knowledge_retriever: object, top_k: int = 5) -> None:
        """Inicializa nó com retriever e configuração."""
        self.retriever = knowledge_retriever
        self.top_k = top_k

    def execute(self, context: dict) -> dict:
        """Executa busca RAG com fallback gracioso."""
        try:
            return self._execute_rag_search(context)
        except Exception as exc:
            logger.warning(
                "rag.unavailable",
                extra={
                    "reason": "search_error",
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            context["rag_available"] = False
            context["rag_unavailable_reason"] = f"search_error: {exc}"
            context["rag_enrichment"] = ""
            return context

    def _execute_rag_search(self, context: dict) -> dict:
        """Lógica interna de busca RAG (pode lançar exceções)."""
        symptoms = context.get("symptoms", [])
        services = context.get("affected_services", [])
        error_summary = context.get("error_summary", "")

        query = (
            f"Services affected: {', '.join(services)}. "
            f"Symptoms: {'; '.join(symptoms)}. "
            f"{error_summary}"
        )

        results = self.retriever.search_similar_incidents(query, top_k=self.top_k)

        if results:
            rag_context = "\n\n".join(r.to_llm_context() for r in results)
            context["rag_enrichment"] = rag_context
            context["rag_available"] = True
            logger.info(
                "rag.enrichment_applied",
                extra={
                    "num_results": len(results),
                    "top_score": results[0].similarity_score,
                },
            )
        else:
            context["rag_enrichment"] = ""
            context["rag_available"] = True  # Disponível mas sem resultados
            logger.info("rag.no_results", extra={"query_length": len(query)})

        return context
