"""Boot script for the local webhook server with all dependencies wired.

Loads .env.local and assembles the FastAPI app with the webhook router, credentials,
dedup store, pipeline runner and Slack notifier.

Usage: python infra/run_webhook_server.py
"""

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Add the project root to the path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Load .env.local before any import that uses os.environ
env_file = project_root / ".env.local"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


from fastapi import FastAPI
import structlog
import uvicorn

from config.loader import AppConfig
from adapters.credentials import EnvCredentialProvider
from interfaces.http.webhook.dedup import DedupStore
from interfaces.http.webhook.pipeline_runner import PipelineRunner
from interfaces.http.webhook.router import webhook_router
from interfaces.http.webhook.errors import webhook_error_handler, WebhookError

# Import normalizers so they self-register via @register_normalizer
import interfaces.http.webhook.normalizers.grafana  # noqa: F401
import interfaces.http.webhook.normalizers.pagerduty  # noqa: F401
import interfaces.http.webhook.normalizers.databricks  # noqa: F401

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

    # Wire dependencies into app.state
    app.state.config = AppConfig()
    app.state.credentials = EnvCredentialProvider()
    app.state.dedup_store = DedupStore()
    app.state.pipeline_runner = PipelineRunner(max_concurrent=3, max_queued=10)

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
    app = create_webhook_app()
    print("=" * 60)
    print("AIOps Agent — Webhook Server")
    print("=" * 60)
    print(f"  Listening:    http://0.0.0.0:47890")
    print(f"  Health:       http://localhost:47890/health")
    print(f"  Webhook:      POST http://localhost:47890/webhook")
    print(f"  .env.local:   {'loaded' if env_file.exists() else 'NOT FOUND'}")
    print(f"  GRAFANA_TOKEN: {'set' if os.environ.get('GRAFANA_WEBHOOK_TOKEN') else 'MISSING'}")
    print(f"  SLACK_TOKEN:   {'set' if os.environ.get('SLACK_BOT_TOKEN') else 'MISSING'}")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=47890, log_level="info")
