from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI

from config.loader import AppConfig, load_config
from core.credentials import EnvCredentialProvider
from core.store import InMemoryStore
from server.health import health_router, set_start_time
from server.webhook.dedup import DedupStore
from server.webhook.errors import register_error_handlers
from server.webhook.pipeline_runner import PipelineRunner
from server.webhook.router import webhook_router

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Gerencia ciclo de vida do servidor — startup e shutdown."""
    set_start_time(time.monotonic())
    log.info("server.starting")

    # Inicializa dependencias no app.state
    config: AppConfig = load_config()
    app.state.config = config
    app.state.store = InMemoryStore()
    app.state.credentials = EnvCredentialProvider()

    # Inicializa dedup store
    app.state.dedup_store = DedupStore()
    log.info("dedup_store.initialized")

    # Importa modulos de normalizers para acionar registro via decorators
    import server.webhook.normalizers.databricks  # noqa: F401
    import server.webhook.normalizers.grafana  # noqa: F401
    import server.webhook.normalizers.pagerduty  # noqa: F401

    from server.webhook.normalizers.registry import NormalizerRegistry
    log.info("normalizers.loaded", sources=NormalizerRegistry.list_sources())

    # Inicializa e inicia pipeline runner
    pipeline_runner = PipelineRunner(
        max_concurrent=config.webhook.max_concurrent_pipelines,
        max_queued=config.webhook.max_queued_alerts,
        store=app.state.store,
    )
    app.state.pipeline_runner = pipeline_runner
    await pipeline_runner.start()

    log.info("server.started")
    yield

    log.info("server.shutting_down")
    # Graceful shutdown — para o pipeline runner e aguarda requisicoes em voo
    await pipeline_runner.stop()
    log.info("server.stopped")


def create_app() -> FastAPI:
    """Factory do FastAPI app — composition root."""
    app = FastAPI(
        title="AIOps Agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Registra routers
    app.include_router(health_router)
    app.include_router(webhook_router, prefix="/api/v1")

    # Registra error handlers RFC 7807
    register_error_handlers(app)

    return app
