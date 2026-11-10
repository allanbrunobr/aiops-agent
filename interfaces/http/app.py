"""
Raiz de composição do AIOps Agent — FastAPI app com MCP SSE opcional.

Responsável por instanciar e conectar todas as dependências.
MCP SSE montado condicionalmente com base em config.mcp.enabled.

História 5.1: Wire MCP SSE no app FastAPI.
História 5.6: Montagem condicional com base em config.mcp.enabled.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from fastapi import FastAPI

from config.loader import AppConfig

if TYPE_CHECKING:
    from rag.knowledge_store import KnowledgeStore

log = structlog.get_logger()


class LazyRAG:
    """Carrega índice RAG sob demanda sem importar sentence-transformers no __init__."""

    def __init__(self, index_path: object = None) -> None:
        self._index_path = Path(index_path) if index_path is not None else Path("data/faiss_index.bin")
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        """Retorna True se o índice já foi carregado em memória."""
        return self._loaded

    def load_persisted_index(self) -> bool:
        """Carrega índice persistido do disco. Retorna False se não existe."""
        if not self._index_path.exists():
            return False
        self._loaded = True
        return True


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
        from adapters.credentials import EnvCredentialProvider
        from interfaces.http.notifications.slack import SlackNotifier

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
    # Rotas de infraestrutura — health (liveness + readiness)
    # --------------------------------------------------------
    from interfaces.http.health import health_router, set_start_time
    import time as _time
    set_start_time(_time.monotonic())
    app.include_router(health_router)

    # --------------------------------------------------------
    # MCP SSE — montado somente se habilitado e transporte = sse
    # --------------------------------------------------------

    # Engine e retriever ANTES do MCP: o SSE precisa recebê-los (ARQ-02).
    # Antes eram criados depois do mount_sse, e diagnose/search ficavam sem eles.
    from app.builders import build_engine, build_rag

    _engine = build_engine(config)
    _, _retriever = build_rag()

    if config.mcp.enabled and config.mcp.transport == "sse":
        from interfaces.mcp.sse import mount_sse
        from interfaces.mcp.rate_limiter import InMemoryRateLimiter
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
            engine=_engine,
            retriever=_retriever,
        )

    # --------------------------------------------------------
    # OAuth 2.1 Authorization Server — sempre montado
    # --------------------------------------------------------
    _oauth = config.mcp.oauth
    _jwt_secret = _oauth.jwt_secret or ""
    _rsa_private_pem = None
    _rsa_public_pem = None
    if _oauth.private_key_path and _oauth.public_key_path:
        from pathlib import Path as _Path
        _priv_path = _Path(_oauth.private_key_path)
        _pub_path = _Path(_oauth.public_key_path)
        if _priv_path.exists() and _pub_path.exists():
            _rsa_private_pem = _priv_path.read_text()
            _rsa_public_pem = _pub_path.read_text()

    from auth.jwt_validator import MIN_HMAC_KEY_BYTES
    _hmac_ok = len(_jwt_secret.encode("utf-8")) >= MIN_HMAC_KEY_BYTES
    _rsa_ok = bool(_rsa_private_pem and _rsa_public_pem)
    if _hmac_ok or _rsa_ok:
        import auth.server as auth_server
        from auth.client_secrets import load_client_secret_hashes
        from auth.token_store import InMemoryTokenStore

        # Hashes fora do repositório (fail-closed se ausente/inválido).
        _secret_hashes = load_client_secret_hashes(_oauth.client_secrets_file)

        auth_server.configure(
            jwt_secret=_jwt_secret if _hmac_ok else "",
            token_ttl=_oauth.token_ttl,
            allowed_clients=[
                {"id": c.id, "scopes": c.scopes,
                 "secret_sha256": c.secret_sha256 or _secret_hashes.get(c.id, "")}
                for c in _oauth.allowed_clients
            ],
            base_url="",
            token_store=InMemoryTokenStore(),
            rsa_private_pem=_rsa_private_pem,
            rsa_public_pem=_rsa_public_pem,
            kid=_oauth.kid,
            issuer=_oauth.issuer,
            audience=_oauth.audience,
        )
        app.include_router(auth_server.router)
        log.info("oauth.server.mounted", clients=len(_oauth.allowed_clients))
    elif _jwt_secret:
        log.warning(
            "oauth.rejected_short_key",
            detail=(
                "jwt_secret is shorter than 32 bytes. "
                "The OAuth server was not mounted. "
                "PyJWT does not treat a short or empty HMAC key as accept-all."
            ),
        )

    # --------------------------------------------------------
    # A2A (Agent-to-Agent) — sempre montado
    # --------------------------------------------------------
    from interfaces.http.routes.a2a import router as a2a_router
    app.include_router(a2a_router)
    log.info("a2a.router.mounted")

    # --------------------------------------------------------
    # app.state — engine e retriever para handlers A2A
    # --------------------------------------------------------
    app.state.config = config
    app.state.a2a_jwt_secret = _jwt_secret if _hmac_ok else ""
    # Mesmos aud/iss do servidor OAuth (vazio = não exige).
    app.state.a2a_jwt_issuer = _oauth.issuer
    app.state.a2a_jwt_audience = _oauth.audience
    app.state.engine = _engine
    app.state.retriever = _retriever
    log.info("a2a.state.populated", engine=type(app.state.engine).__name__)

    # --------------------------------------------------------
    # Validação de configuração OAuth
    # --------------------------------------------------------
    if config.mcp.auth_method == "oauth21" and not _hmac_ok:
        log.warning(
            "oauth.misconfiguration",
            detail=(
                "auth_method=oauth21 without a 32-byte jwt_secret. "
                "The OAuth server was not mounted. "
                "PyJWT rejects an empty HMAC key; it does not accept every token."
            ),
        )

    # Phase 2 — avisa se federated sem chaves RSA configuradas
    if config.mcp.auth_method == "federated":
        oauth = config.mcp.oauth
        if not oauth.private_key_path or not oauth.public_key_path:
            log.warning(
                "federation.no_rsa_keys",
                detail="auth_method=federated sem private_key_path/public_key_path — JWKS endpoint retornará conjunto vazio",
            )
        if not oauth.trusted_issuers:
            log.warning(
                "federation.no_trusted_issuers",
                detail="auth_method=federated sem trusted_issuers — nenhum token externo será aceito",
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


# ------------------------------------------------------------------ #
# Estado global para injeção de dependência (server/dependencies.py)  #
# ------------------------------------------------------------------ #

from dataclasses import dataclass as _dataclass


@_dataclass
class AppState:
    """Estado global da aplicação para injeção de dependência FastAPI."""

    config: AppConfig | None = None
    store: object = None  # IncidentStore
    engine: object = None  # BlueprintEngine
    knowledge_store: KnowledgeStore | None = None


app_state = AppState()

# Compatibilidade retroativa — referenciado por server/handlers/false_positive.py
knowledge_store: KnowledgeStore | None = None
