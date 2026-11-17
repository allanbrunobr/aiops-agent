"""Regressão F3-5: rotas knowledge/admin montadas e funcionais (não órfãs).

Antes do fix: routes/knowledge.py e routes/admin.py nunca eram montadas em
produção; get_knowledge_store do knowledge era placeholder NotImplementedError.
Decisão registrada: MONTAR (as implementações reais existem — KnowledgeStore
e ValidationRunner); store isolado por app.state no create_app.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_knowledge_routes_mounted_and_functional(tmp_path):
    from interfaces.http.app import create_app
    from rag.knowledge_store import KnowledgeStore

    app = create_app()
    # Store isolado — não toca data/knowledge/patterns.json real
    app.state.knowledge_store = KnowledgeStore(
        storage_path=str(tmp_path / "patterns.json")
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/knowledge/patterns")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        resp = await client.post(
            "/api/v1/knowledge/patterns",
            json={
                "service": "payment-svc",
                "symptoms": ["Connection pool exhausted"],
                "root_cause": "max_connections reduced by config deploy",
                "resolution": "Revert config to restore max_connections=200",
                "tags": ["connection-pool"],
                "confidence": 0.9,
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["pattern_id"].startswith("pat-")

        resp = await client.get("/api/v1/knowledge/patterns")
        assert resp.json()["total"] == 1


@pytest.mark.integration
def test_admin_validation_route_mounted():
    """Rota admin montada no app de produção (antes era órfã).

    Via OpenAPI schema — o FastAPI desta versão expõe routers incluídos
    como _IncludedRouter (sem .path) em app.routes.
    """
    from interfaces.http.app import create_app

    app = create_app()
    paths = app.openapi()["paths"]
    assert "/api/v1/admin/validate/knowledge" in paths
    assert "/api/v1/knowledge/patterns" in paths


@pytest.mark.integration
def test_admin_validation_scenarios_functional_in_subprocess():
    """ValidationRunner real (FAISS + embeddings) em SUBPROCESSO.

    Chamar a rota em processo carregaria torch/faiss/sentence_transformers
    no sys.modules do pytest, violando a invariante TestMLIsolation. O
    handler da rota é um wrapper fino deste runner (mesmos 3 cenários).
    """
    import json
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).parent.parent
    script = (
        "import json;"
        "from validation.runner import ValidationRunner;"
        "from validation.scenarios.knowledge_write_read import KnowledgeWriteReadScenario;"
        "from validation.scenarios.rag_degradation import RAGDegradationScenario;"
        "from validation.scenarios.rag_retrieval import RAGRetrievalScenario;"
        "r = ValidationRunner(subsystem='knowledge');"
        "r.register(RAGRetrievalScenario());"
        "r.register(RAGDegradationScenario());"
        "r.register(KnowledgeWriteReadScenario());"
        "rep = r.run_all();"
        "print(json.dumps({'subsystem': rep.subsystem,"
        " 'scenarios_run': rep.scenarios_run,"
        " 'passed': rep.passed, 'failed': rep.failed}))"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(root),
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    report = json.loads(proc.stdout.strip().splitlines()[-1])
    assert report["subsystem"] == "knowledge"
    assert report["scenarios_run"] == 3
    assert report["failed"] == 0, report
