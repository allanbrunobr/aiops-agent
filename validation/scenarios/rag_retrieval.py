from __future__ import annotations

from dataclasses import dataclass

from rag.models import KnowledgePattern


@dataclass
class CheckResult:
    """Resultado de um check individual."""

    passed: bool
    details: str = ""
    expected: str = ""
    actual: str = ""


class RAGRetrievalScenario:
    """Valida acurácia de busca RAG com padrões conhecidos.

    Seed: padrão payment-pool-exhaustion de data/sample_incident.py.
    """

    name = "rag_retrieval_accuracy"

    def execute(self, knowledge_store: object, incident_store: object, temp_dir: str) -> CheckResult:
        """Executa validação de retrieval RAG."""
        from rag.indexer import ObservabilityIndexer

        # 1. Seed com padrão payment-svc
        payment_pattern = KnowledgePattern(
            pattern_id="pat-baseline-001",
            service="payment-svc",
            symptoms=[
                "Connection pool exhausted",
                "Database connection timeout",
                "Circuit breaker open",
            ],
            root_cause=(
                "Connection pool exhaustion after config deploy "
                "reduced max_connections from 200 to 50"
            ),
            resolution=(
                "Revert config to restore max_connections=200, "
                "then investigate config change process"
            ),
            tags=["connection-pool", "config-change", "database"],
            confidence=0.95,
        )
        knowledge_store.add_pattern(payment_pattern)

        # 2. Build índice FAISS (síncrono, dados pequenos)
        indexer = ObservabilityIndexer()
        patterns = knowledge_store.get_all_patterns()
        texts = [p.to_embedding_text() for p in patterns]

        try:
            import numpy as np
            import faiss

            indexer._load_model()
            embeddings = indexer.model.encode(texts, normalize_embeddings=True)
            embeddings = np.array(embeddings, dtype=np.float32)
            indexer.index = faiss.IndexFlatIP(embeddings.shape[1])
            indexer.index.add(embeddings)
        except Exception as exc:
            return CheckResult(
                passed=False,
                details=f"Failed to build FAISS index: {exc}",
                expected="FAISS index built",
                actual=f"Error: {exc}",
            )

        # 3. Query com sintomas similares
        from rag.knowledge_retriever import KnowledgeRetriever

        retriever = KnowledgeRetriever(indexer, patterns)
        results = retriever.search_similar_incidents(
            query="payment service connection pool exhausted, database timeouts",
            top_k=5,
        )

        if not results:
            return CheckResult(
                passed=False,
                details="No results returned for known pattern query",
                expected="payment-svc pattern in top-K",
                actual="empty results",
            )

        found = any(r.pattern.pattern_id == "pat-baseline-001" for r in results)
        top_score = results[0].similarity_score

        if not found:
            return CheckResult(
                passed=False,
                details="Known pattern not found in top-K results",
                expected="pat-baseline-001 in results",
                actual=f"Results: {[r.pattern.pattern_id for r in results]}",
            )

        return CheckResult(
            passed=True,
            details=f"Pattern found with top score {top_score:.3f}",
            expected="pat-baseline-001 in top-K",
            actual=f"Found at score {top_score:.3f}",
        )
