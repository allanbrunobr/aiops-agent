from __future__ import annotations

from fastapi import FastAPI

from core.store import InMemoryStore
from rag.knowledge_store import KnowledgeStore
from server.api.accuracy import router as accuracy_router, get_store
from server.api.metrics import router as metrics_router, get_metrics_collector
from server.health import router as health_router, set_metrics_collector
from server.metrics.pipeline import PipelineMetricsCollector
from server.routes.admin import router as admin_router
from server.routes.knowledge import router as knowledge_router, get_knowledge_store

# Composition root — instâncias únicas compartilhadas
incident_store = InMemoryStore()
knowledge_store = KnowledgeStore(storage_path="data/knowledge/patterns.json")
pipeline_metrics = PipelineMetricsCollector(retention_hours=24)

# Carrega padrões existentes do disco
knowledge_store.load_from_disk()

app = FastAPI(
    title="AIOps Agent API",
    description="API para monitoramento e feedback do agente AIOps",
    version="1.0.0",
)

# Routers
app.include_router(health_router)
app.include_router(accuracy_router)
app.include_router(metrics_router)
app.include_router(knowledge_router)
app.include_router(admin_router)

# Injeta dependências via dependency_overrides
app.dependency_overrides[get_store] = lambda: incident_store
app.dependency_overrides[get_metrics_collector] = lambda: pipeline_metrics
app.dependency_overrides[get_knowledge_store] = lambda: knowledge_store

# Configura health endpoint com métricas
set_metrics_collector(pipeline_metrics)
