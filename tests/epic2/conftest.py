"""Configuracao de testes para Epic 2.

ASGITransport nao executa o lifespan do FastAPI automaticamente.
Os fixtures abaixo inicializam o app.state manualmente para testes
que dependem de store, credentials, dedup e pipeline_runner.
"""
from __future__ import annotations

import time

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Restringe testes anyio ao backend asyncio (trio nao instalado)."""
    return "asyncio"


def _ensure_normalizers_registered() -> None:
    """Garante que os normalizers estao registrados no registry global.

    Importar o modulo so funciona na primeira vez (Python cacheia imports).
    Apos clear() do registry, registramos as instancias diretamente.
    """
    from server.webhook.normalizers.databricks import DatabricksNormalizer
    from server.webhook.normalizers.grafana import GrafanaNormalizer
    from server.webhook.normalizers.pagerduty import PagerDutyNormalizer
    from server.webhook.normalizers.registry import NormalizerRegistry

    if "pagerduty" not in NormalizerRegistry.list_sources():
        NormalizerRegistry.register("pagerduty", PagerDutyNormalizer())
    if "grafana" not in NormalizerRegistry.list_sources():
        NormalizerRegistry.register("grafana", GrafanaNormalizer())
    if "databricks" not in NormalizerRegistry.list_sources():
        NormalizerRegistry.register("databricks", DatabricksNormalizer())


@pytest.fixture
def app():
    """App FastAPI com estado inicializado (sem lifespan).

    Inicializa app.state manualmente porque ASGITransport nao
    executa o lifespan. Registra normalizers se necessario.
    """
    from adapters.persistence.in_memory_store import InMemoryStore
    from config.loader import load_config
    from core.credentials import EnvCredentialProvider
    from server.app import create_app
    from server.health import set_start_time
    from server.webhook.dedup import DedupStore

    _ensure_normalizers_registered()

    fastapi_app = create_app()
    config = load_config()
    fastapi_app.state.config = config
    fastapi_app.state.store = InMemoryStore()
    fastapi_app.state.credentials = EnvCredentialProvider()
    fastapi_app.state.dedup_store = DedupStore()
    set_start_time(time.monotonic())

    return fastapi_app


@pytest.fixture
async def app_with_pipeline(app):
    """App com PipelineRunner inicializado (requer event loop asyncio)."""
    from server.webhook.pipeline_runner import PipelineRunner

    config = app.state.config
    runner = PipelineRunner(
        max_concurrent=config.webhook.max_concurrent_pipelines,
        max_queued=config.webhook.max_queued_alerts,
        store=app.state.store,
    )
    await runner.start()
    app.state.pipeline_runner = runner
    yield app
    await runner.stop()
