"""
Raiz de composição do AIOps Agent — FastAPI app com MCP SSE opcional.

Responsável por instanciar e conectar todas as dependências.
MCP SSE montado condicionalmente com base em config.mcp.enabled.

História 5.1: Wire MCP SSE no app FastAPI.
História 5.6: Montagem condicional com base em config.mcp.enabled.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI

from config.loader import AppConfig

log = structlog.get_logger()


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Cria aplicação FastAPI com montagem condicional do MCP.

    Composição de dependências:
      1. Carrega config (padrão ou fornecida)
      2. Inicializa safety layers
      3. Inicializa rate limiter
      4. Monta MCP SSE se config.mcp.enabled e transport == "sse"

    Args:
        config: AppConfig pré-carregado (None = carrega defaults).

    Returns:
        FastAPI app configurado e pronto para inicialização.
    """
    if config is None:
        config = AppConfig()

    app = FastAPI(
        title="AIOps Agent",
        description="Agentic incident response platform with MCP Toolshed",
        version="1.0.0",
    )

    # --------------------------------------------------------
    # Rotas de infraestrutura
    # --------------------------------------------------------

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint."""
        return {
            "status": "ok",
            "mcp_enabled": config.mcp.enabled,
            "mcp_transport": config.mcp.transport,
        }

    # --------------------------------------------------------
    # MCP SSE — montado somente se habilitado e transporte = sse
    # --------------------------------------------------------

    if config.mcp.enabled and config.mcp.transport == "sse":
        from mcp_server.sse import mount_sse
        from mcp_server.rate_limiter import InMemoryRateLimiter
        from safety.pre_validator import PreExecutionValidator
        from safety.tnr import TNRSafetyGuard
        from safety.version_validator import VersionValidator
        from pipeline.telemetry_sanitizer import TelemetrySanitizer

        # Instancia safety layers
        sanitizer = TelemetrySanitizer()
        pre_validator = PreExecutionValidator()
        tnr = TNRSafetyGuard(
            regression_threshold=config.thresholds.regression_threshold
        )
        version_validator = VersionValidator()

        # Instancia rate limiter com config
        rate_limiter = InMemoryRateLimiter.from_config(config.mcp.rate_limits)

        mount_sse(
            app,
            config=config.mcp,
            rate_limiter=rate_limiter,
            sanitizer=sanitizer,
            pre_validator=pre_validator,
            tnr=tnr,
            version_validator=version_validator,
        )

    return app


async def handle_prediction_event(
    event_type: str,
    data: dict,
    slack_notifier: object,
) -> None:
    """Handler de evento de predição — envia notificação Slack proativa.

    Registra handlers para 'prediction.warning' e 'prediction.alert'.
    Erros de Slack são logados e nunca propagados ao pipeline.

    Args:
        event_type: "prediction.warning" ou "prediction.alert".
        data: Dados do PredictionReport serializado como dict.
        slack_notifier: Instância de SlackNotifier configurada.
    """
    try:
        blocks = slack_notifier._build_prediction_blocks(data)  # type: ignore[union-attr]

        client = getattr(slack_notifier, "_client", None)
        dry_run = getattr(slack_notifier, "_dry_run", False)
        channel = getattr(slack_notifier, "_channel", "#alerts")
        incident_id = data.get("incident_id", "unknown")

        if dry_run:
            log.info(
                "slack.dry_run",
                action="prediction_event",
                event_type=event_type,
                correlation_id=incident_id,
                blocks=blocks,
            )
            return

        if client is None:
            return

        color = "#ff0000" if event_type == "prediction.alert" else "#ffcc00"
        await client.chat_postMessage(
            channel=channel,
            attachments=[{"color": color, "blocks": blocks}],
            text=f"Prediction event: {event_type}",
        )
        log.info(
            "slack.prediction_sent",
            event_type=event_type,
            correlation_id=incident_id,
        )
    except Exception:
        log.warning(
            "slack.prediction_event_error",
            event_type=event_type,
            exc_info=True,
        )
