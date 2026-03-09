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


class KnowledgeWriteReadScenario:
    """Valida ciclo completo: escrita -> rebuild -> leitura na base de conhecimento."""

    name = "knowledge_write_read_cycle"

    def execute(self, knowledge_store: object, incident_store: object, temp_dir: str) -> CheckResult:
        """Executa ciclo write/read com rebuild de índice."""
        from rag.indexer import ObservabilityIndexer
        from rag.knowledge_retriever import KnowledgeRetriever

        # 1. Insere padrão
        test_pattern = KnowledgePattern(
            pattern_id="pat-test-writeread",
            service="test-svc",
            symptoms=["CPU spike", "Memory leak", "OOM kills"],
            root_cause="Memory leak in request handler causing OOM",
            resolution="Restart pods and deploy fix for memory leak",
            tags=["memory", "oom"],
            confidence=0.8,
        )
        knowledge_store.add_pattern(test_pattern)

        # 2. Verifica flag stale
        if not knowledge_store.is_index_stale:
            return CheckResult(
                passed=False,
                details="Index not marked stale after pattern addition",
                expected="is_index_stale = True",
                actual="is_index_stale = False",
            )

        # 3. Rebuild índice (síncrono — dados pequenos em teste)
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
                expected="FAISS index rebuilt",
                actual=f"Error: {exc}",
            )

        knowledge_store.mark_index_rebuilt()

        # 4. Busca pelo padrão inserido
        retriever = KnowledgeRetriever(indexer, patterns)
        results = retriever.search_similar_incidents(
            query="memory leak OOM kill CPU spike",
            top_k=5,
        )

        found = any(r.pattern.pattern_id == "pat-test-writeread" for r in results)

        if not found:
            return CheckResult(
                passed=False,
                details="Inserted pattern not found after rebuild",
                expected="pat-test-writeread in results",
                actual=f"Results: {[r.pattern.pattern_id for r in results]}",
            )

        # 5. Verifica flag stale resetado
        if knowledge_store.is_index_stale:
            return CheckResult(
                passed=False,
                details="Index still marked stale after rebuild",
                expected="is_index_stale = False",
                actual="is_index_stale = True",
            )

        return CheckResult(
            passed=True,
            details="Write/read cycle complete: pattern inserted, index rebuilt, pattern found",
            expected="Pattern retrievable after rebuild",
            actual="Pattern found in search results",
        )
