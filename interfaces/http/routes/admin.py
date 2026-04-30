from __future__ import annotations

import asyncio

from fastapi import APIRouter

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@router.post("/validate/knowledge")
async def validate_knowledge() -> dict:
    """Executa cenários de validação para knowledge base e feedback.

    Usa estado isolado — não afeta dados de produção.
    """
    from validation.runner import ValidationRunner
    from validation.scenarios.rag_retrieval import RAGRetrievalScenario
    from validation.scenarios.rag_degradation import RAGDegradationScenario
    from validation.scenarios.knowledge_write_read import KnowledgeWriteReadScenario

    runner = ValidationRunner(subsystem="knowledge")
    runner.register(RAGRetrievalScenario())
    runner.register(RAGDegradationScenario())
    runner.register(KnowledgeWriteReadScenario())

    # Executa em thread pool (CPU-bound: FAISS + embeddings)
    loop = asyncio.get_event_loop()
    report = await loop.run_in_executor(None, runner.run_all)

    return {
        "subsystem": report.subsystem,
        "summary": report.summary(),
        "scenarios_run": report.scenarios_run,
        "passed": report.passed,
        "failed": report.failed,
        "total_duration_ms": round(report.total_duration_ms, 1),
        "results": [
            {
                "name": r.name,
                "passed": r.passed,
                "duration_ms": round(r.duration_ms, 1),
                "details": r.details,
                "expected": r.expected,
                "actual": r.actual,
            }
            for r in report.results
        ],
    }
