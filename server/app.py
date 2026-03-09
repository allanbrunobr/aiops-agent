"""FastAPI application — composition root e lifecycle de recursos.

Ponto de entrada do servidor: uvicorn server.app:app
Todas as dependencias sao instanciadas aqui e injetadas via construtor.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from blueprint.engine import BlueprintEngine
from config.loader import AppConfig, load_config
from core.credentials import EnvCredentialProvider
from core.logging import configure_logging, get_logger
from core.store import InMemoryStore

log = get_logger("server")

# Caminho do indice FAISS persistido
FAISS_INDEX_PATH = Path("data/index.faiss")


class AppState:
    """Estado compartilhado da aplicacao — montado uma vez no lifespan."""

    def __init__(self) -> None:
        self.config: AppConfig | None = None
        self.store: InMemoryStore | None = None
        self.creds: EnvCredentialProvider | None = None
        self.engine: BlueprintEngine | None = None
        self.rag: LazyRAG | None = None


app_state = AppState()


class LazyRAG:
    """Wrapper para RAG que adia import de sentence-transformers e FAISS.

    Garante cold start < 10s — modelos ML so carregam no primeiro uso.
    """

    def __init__(self, index_path: Path = FAISS_INDEX_PATH) -> None:
        """Inicializa sem carregar modelos pesados."""
        self._index_path = index_path
        self._indexer = None
        self._retriever = None
        self._index_loaded = False

    def _ensure_loaded(self) -> None:
        """Lazy-load de sentence-transformers e FAISS no primeiro uso."""
        if self._indexer is not None:
            return

        # Import pesado — so acontece aqui, nunca no module level
        from rag.indexer import ObservabilityIndexer
        from rag.retriever import ObservabilityRetriever

        self._indexer = ObservabilityIndexer()
        self._retriever = ObservabilityRetriever(self._indexer)

    def load_persisted_index(self) -> bool:
        """Carrega indice FAISS do disco se disponivel.

        Returns:
            True se indice carregado com sucesso, False se nao existe.
        """
        if not self._index_path.exists():
            return False

        try:
            import faiss
            self._ensure_loaded()
            self._indexer.index = faiss.read_index(str(self._index_path))
            self._index_loaded = True
            log.info(
                "rag.index_loaded",
                path=str(self._index_path),
                source="persisted",
            )
            return True
        except Exception as exc:
            log.warning("rag.index_load_failed", error=str(exc))
            return False

    def rebuild_index(self, data: list) -> None:
        """Reconstroi indice FAISS com dados frescos e persiste no disco."""
        self._ensure_loaded()
        self._indexer.index_data(data)

        # Persiste no disco para proximos cold starts
        try:
            import faiss
            self._index_path.parent.mkdir(parents=True, exist_ok=True)
            faiss.write_index(self._indexer.index, str(self._index_path))
            log.info("rag.index_persisted", path=str(self._index_path))
        except Exception as exc:
            log.warning("rag.index_persist_failed", error=str(exc))

        self._index_loaded = True

    @property
    def retriever(self):
        """Retorna retriever, forcando lazy-load se necessario."""
        self._ensure_loaded()
        return self._retriever

    @property
    def is_loaded(self) -> bool:
        """Verifica se o indice FAISS esta carregado."""
        return self._index_loaded


async def _rebuild_index_background(rag: LazyRAG) -> None:
    """Reconstroi indice FAISS em background apos startup.

    Usa run_in_executor para nao bloquear o event loop (CPU-bound).
    """
    log.info("rag.background_rebuild_started")
    try:
        loop = asyncio.get_event_loop()

        # Gera dados simulados para rebuild (sera substituido por dados reais em E2)
        from data.sample_incident import create_incident
        incident = create_incident(seed=42)

        # CPU-bound: executa em thread separada
        await loop.run_in_executor(
            None,
            rag.rebuild_index,
            incident.logs + [str(m) for m in incident.metrics],
        )
        log.info("rag.background_rebuild_completed")
    except Exception as exc:
        log.error("rag.background_rebuild_failed", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan do FastAPI — composition root e lifecycle de recursos."""
    # === STARTUP ===
    configure_logging()
    log.info("server.starting")

    # 1. Config
    config = load_config()
    app_state.config = config

    # 2. Core dependencies
    app_state.store = InMemoryStore()
    app_state.creds = EnvCredentialProvider()

    # 3. Blueprint Engine
    app_state.engine = BlueprintEngine(config, app_state.store, app_state.creds)

    # 4. RAG (lazy)
    app_state.rag = LazyRAG()

    # 5. FAISS index lifecycle
    if app_state.rag.load_persisted_index():
        # Indice existente carregado — rebuild em background
        asyncio.create_task(_rebuild_index_background(app_state.rag))
    else:
        # Primeiro run — rebuild sincrono (bloqueia ate pronto)
        log.info("rag.first_run_sync_rebuild")
        # Nao bloqueia startup se modelo nao disponivel
        try:
            from data.sample_incident import create_incident
            incident = create_incident(seed=42)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                app_state.rag.rebuild_index,
                incident.logs + [str(m) for m in incident.metrics],
            )
        except Exception as exc:
            log.warning("rag.sync_rebuild_failed", error=str(exc))

    log.info("server.started")

    yield

    # === SHUTDOWN ===
    log.info("server.stopping")


def create_app() -> FastAPI:
    """Factory function para criar a aplicacao FastAPI."""
    application = FastAPI(
        title="AIOps Agent",
        version="1.0.0",
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health() -> JSONResponse:
        """Liveness check — processo vivo, resposta rapida."""
        return JSONResponse({"status": "ok"})

    @application.get("/health/ready")
    async def health_ready() -> JSONResponse:
        """Readiness check — dependencias carregadas."""
        checks = {
            "config": app_state.config is not None,
            "store": app_state.store is not None,
            "engine": app_state.engine is not None,
            "rag_index": app_state.rag is not None and app_state.rag.is_loaded,
        }
        all_ready = all(checks.values())
        return JSONResponse(
            {"status": "ready" if all_ready else "not_ready", "checks": checks},
            status_code=200 if all_ready else 503,
        )

    return application


# Ponto de entrada para uvicorn: uvicorn server.app:app
app = create_app()
