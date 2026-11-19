"""Boot script for the local webhook server with all dependencies wired.

Loads .env.local and assembles the FastAPI app with the webhook router, credentials,
dedup store, pipeline runner and Slack notifier.

Usage: python infra/run_webhook_server.py
"""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add the project root to the path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Load .env / .env.local before any import that uses os.environ (F5-3: single loader)
from config.env import load_local_env

load_local_env(project_root)


import structlog
import uvicorn
from fastapi import FastAPI

import interfaces.http.webhook.normalizers.databricks  # noqa: F401

# Import normalizers so they self-register via @register_normalizer
import interfaces.http.webhook.normalizers.grafana  # noqa: F401
import interfaces.http.webhook.normalizers.pagerduty  # noqa: F401
from adapters.credentials import EnvCredentialProvider
from config.loader import AppConfig
from interfaces.http.webhook.dedup import DedupStore
from interfaces.http.webhook.errors import WebhookError, webhook_error_handler
from interfaces.http.webhook.pipeline_runner import PipelineRunner
from interfaces.http.webhook.router import webhook_router

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle: start the pipeline runner on startup, stop it on shutdown."""
    runner: PipelineRunner = app.state.pipeline_runner
    await runner.start()
    log.info("webhook_server.started", port=47890)
    yield
    await runner.stop()
    log.info("webhook_server.stopped")


def create_webhook_app() -> FastAPI:
    """Create the FastAPI app with the webhook router and all dependencies."""
    app = FastAPI(
        title="AIOps Agent — Webhook Server",
        description="Receives alerts from Grafana/PagerDuty/Databricks and processes them through the pipeline",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Wire dependencies into app.state (F3-3: runner com engine/store/notifier reais)
    from adapters.persistence.in_memory_store import InMemoryStore
    from app.builders import build_engine
    from interfaces.http.notifications.slack import SlackNotifier

    config = AppConfig()
    app.state.config = config
    app.state.credentials = EnvCredentialProvider()
    app.state.dedup_store = DedupStore()

    store = InMemoryStore()
    app.state.incident_store = store
    engine = build_engine(config, store=store)

    # Slack: dry-run por padrão; envio real só com token E opt-in explícito
    # (AIOPS_WEBHOOK_SLACK_REAL=1) — mesmo princípio do demo (F3-4)
    slack_real = os.environ.get("AIOPS_WEBHOOK_SLACK_REAL") == "1" and bool(
        os.environ.get("SLACK_BOT_TOKEN")
    )
    notifier = SlackNotifier(
        channel=os.environ.get("SLACK_CHANNEL", "#incidents"),
        creds=app.state.credentials,
        dry_run=not slack_real,
    )

    app.state.pipeline_runner = PipelineRunner(
        max_concurrent=config.webhook.max_concurrent_pipelines,
        max_queued=config.webhook.max_queued_alerts,
        store=store,
        engine=engine,
        config=config,
        notifier=notifier,
    )

    # Mount the webhook router
    app.include_router(webhook_router)

    # Error handler for WebhookError -> ProblemDetail JSON
    app.add_exception_handler(WebhookError, webhook_error_handler)

    # Health check
    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "pipeline_active": app.state.pipeline_runner.active_count,
            "pipeline_queued": app.state.pipeline_runner.queued_count,
            "dedup_size": app.state.dedup_store.size,
        }

    return app


if __name__ == "__main__":
    from core.logging import configure_logging

    configure_logging(os.environ.get("AIOPS_LOG_LEVEL", "INFO"))
    app = create_webhook_app()
    print("=" * 60)
    print("AIOps Agent — Webhook Server")
    print("=" * 60)
    print("  Listening:    http://127.0.0.1:47890")
    print("  Health:       http://localhost:47890/health")
    print("  Webhook:      POST http://localhost:47890/webhook")
    _env_local_loaded = (project_root / ".env.local").is_file()
    print(f"  .env.local:   {'loaded' if _env_local_loaded else 'NOT FOUND'}")
    print(
        f"  GRAFANA_TOKEN: {'set' if os.environ.get('GRAFANA_WEBHOOK_TOKEN') else 'MISSING'}"
    )
    print(
        f"  SLACK_TOKEN:   {'set' if os.environ.get('SLACK_BOT_TOKEN') else 'MISSING'}"
    )
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=47890, log_level="info")
