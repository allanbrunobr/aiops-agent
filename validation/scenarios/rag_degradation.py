from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CheckResult:
    """Resultado de um check individual."""

    passed: bool
    details: str = ""
    expected: str = ""
    actual: str = ""


class RAGDegradationScenario:
    """Valida degradação graciosa quando FAISS está indisponível."""

    name = "rag_graceful_degradation"

    def execute(self, knowledge_store: object, incident_store: object, temp_dir: str) -> CheckResult:
        """Simula índice FAISS indisponível e verifica degradação graciosa."""
        from rag.indexer import ObservabilityIndexer
        from rag.knowledge_retriever import KnowledgeRetriever

        # Cria retriever com índice None (simula indisponibilidade)
        indexer = ObservabilityIndexer()
        # Não carrega modelo nem constrói índice — simula falha
        retriever = KnowledgeRetriever(indexer, [])

        try:
            results = retriever.search_similar_incidents(
                query="payment service timeout",
                top_k=5,
            )
        except Exception as exc:
            return CheckResult(
                passed=False,
                details=f"Exception raised during degraded search: {exc}",
                expected="Empty results, no exception",
                actual=f"Exception: {type(exc).__name__}: {exc}",
            )

        if results:
            return CheckResult(
                passed=False,
                details="Expected empty results when index unavailable",
                expected="empty results",
                actual=f"{len(results)} results returned",
            )

        return CheckResult(
            passed=True,
            details="Graceful degradation: empty results returned without exception",
            expected="empty results, no exception",
            actual="empty results, no exception",
        )
