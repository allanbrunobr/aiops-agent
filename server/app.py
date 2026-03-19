"""
Raiz de composição do AIOps Agent — FastAPI app com MCP SSE opcional.

Responsável por instanciar e conectar todas as dependências.
MCP SSE montado condicionalmente com base em config.mcp.enabled.

História 5.1: Wire MCP SSE no app FastAPI.
História 5.6: Montagem condicional com base em config.mcp.enabled.
"""

from __future__ import annotations

from fastapi import FastAPI

from config.loader import AppConfig


class NotificationEngine:
    """Container leve de observers de notificacao — Story 3.6."""

    def __init__(self) -> None:
        self._observers: list = []

    def add_observer(self, observer: object) -> None:
        self._observers.append(observer)


def create_engine(config: AppConfig) -> NotificationEngine:
    """Cria NotificationEngine com observers configurados.

    Adiciona SlackNotifier como observer se notifications.slack.enabled=True
    e token disponivel via EnvCredentialProvider.

    Args:
        config: Configuracao do sistema.

    Returns:
        NotificationEngine com observers registrados.
    """
    engine = NotificationEngine()
    slack_cfg = config.notifications.slack
    if slack_cfg.enabled:
        from core.credentials import EnvCredentialProvider
        from server.notifications.slack import SlackNotifier

        creds = EnvCredentialProvider()
        if creds.get("SLACK_BOT_TOKEN") is not None:
            notifier = SlackNotifier(
                channel=slack_cfg.channel,
                creds=creds,
                max_retries=slack_cfg.max_retries,
                initial_backoff_s=slack_cfg.initial_backoff_s,
                fallback_dir=slack_cfg.fallback_dir,
                dry_run=slack_cfg.dry_run,
            )
            engine.add_observer(notifier)
    return engine


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
