"""Testes para o modulo de notificacoes Slack.

Cobre Stories 3.1 (Slack Bot API), 3.2 (Retry + Fallback) e 3.6 (Config YAML).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from core.lifecycle import LifecycleEvent, LifecycleObserver
from interfaces.http.notifications.fallback import LocalFallbackWriter
from interfaces.http.notifications.slack import SlackNotifier
from core.credentials import EnvCredentialProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    event_type: str,
    correlation_id: str = "inc-20260308-test1234",
    node_name: str | None = None,
    data: dict | None = None,
) -> LifecycleEvent:
    """Cria evento de ciclo de vida para testes."""
    return LifecycleEvent(
        event_type=event_type,
        node_name=node_name,
        correlation_id=correlation_id,
        timestamp=datetime.utcnow(),
        data=data or {},
    )


def _make_notifier(
    mock_client: AsyncMock,
    channel: str = "C12345",
    max_retries: int = 3,
    initial_backoff_s: float = 1.0,
    fallback_dir: str = "output",
) -> SlackNotifier:
    """Cria SlackNotifier com cliente mockado."""
    notifier = SlackNotifier.__new__(SlackNotifier)
    notifier._client = mock_client
    notifier._channel = channel
    notifier._max_retries = max_retries
    notifier._initial_backoff_s = initial_backoff_s
    notifier._backoff_multiplier = 2.0
    notifier._fallback_dir = Path(fallback_dir)
    notifier._dry_run = False
    notifier._message_ts = {}
    notifier._channel_ids = {}
    notifier._completed_nodes = {}
    return notifier


def _make_slack_error(code: str) -> SlackApiError:
    """Cria SlackApiError com codigo especificado."""
    response = MagicMock()
    response.get.return_value = code
    response.__getitem__ = lambda self, key: code if key == "error" else None
    return SlackApiError(f"Slack error: {code}", response=response)


# ---------------------------------------------------------------------------
# Story 3.1 — Tests 8.1-8.7
# ---------------------------------------------------------------------------

class TestSlackNotifierInitialMessage:
    """8.1: SlackNotifier posta mensagem inicial no evento pipeline.started."""

    @pytest.mark.asyncio
    async def test_posts_initial_message_on_pipeline_started(self):
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "1234567890.123456"}
        notifier = _make_notifier(mock_client)

        event = _make_event(
            "pipeline.started",
            data={"title": "High error rate", "severity": "critical", "service": "payment-svc"},
        )
        await notifier.on_event(event)

        mock_client.chat_postMessage.assert_called_once()
        call_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C12345"
        assert "blocks" in call_kwargs
        assert notifier._message_ts["inc-20260308-test1234"] == "1234567890.123456"

    @pytest.mark.asyncio
    async def test_initial_blocks_contain_incident_info(self):
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "111.222"}
        notifier = _make_notifier(mock_client)

        event = _make_event(
            "pipeline.started",
            data={"title": "DB timeout", "severity": "high", "service": "auth-svc"},
        )
        await notifier.on_event(event)

        blocks = mock_client.chat_postMessage.call_args.kwargs["blocks"]
        # Header com ID do incidente
        assert any("inc-20260308-test1234" in str(b) for b in blocks)
        # Status Diagnosing
        assert any("Diagnosing" in str(b) for b in blocks)


class TestSlackNotifierProgressUpdate:
    """8.2: SlackNotifier atualiza mensagem no evento node.completed."""

    @pytest.mark.asyncio
    async def test_updates_message_on_node_completed(self):
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "999.111"}
        notifier = _make_notifier(mock_client)

        # Primeiro, envia mensagem inicial para ter ts registrado
        start_event = _make_event("pipeline.started")
        await notifier.on_event(start_event)

        # Agora simula node completed
        node_event = _make_event(
            "node.completed",
            node_name="log_parser",
            data={"duration_ms": 150},
        )
        await notifier.on_event(node_event)

        mock_client.chat_update.assert_called_once()
        call_kwargs = mock_client.chat_update.call_args.kwargs
        assert call_kwargs["ts"] == "999.111"
        assert call_kwargs["channel"] == "C12345"

    @pytest.mark.asyncio
    async def test_tracks_completed_nodes(self):
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "555.666"}
        notifier = _make_notifier(mock_client)

        cid = "inc-20260308-test1234"
        await notifier.on_event(_make_event("pipeline.started"))
        await notifier.on_event(_make_event("node.completed", node_name="log_parser"))
        await notifier.on_event(_make_event("node.completed", node_name="metric_filter"))

        assert "log_parser" in notifier._completed_nodes[cid]
        assert "metric_filter" in notifier._completed_nodes[cid]


class TestSlackNotifierFinalMessage:
    """8.3: SlackNotifier posta mensagem final Block Kit no evento pipeline.completed."""

    @pytest.mark.asyncio
    async def test_posts_final_block_kit_message(self):
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "100.200"}
        notifier = _make_notifier(mock_client)

        await notifier.on_event(_make_event("pipeline.started"))
        final_event = _make_event(
            "pipeline.completed",
            data={
                "root_cause": "Connection pool exhausted",
                "confidence": 0.92,
                "evidence": ["Error rate spike", "Pool metrics"],
                "proposed_fix": "kubectl scale deployment payment-svc --replicas=5",
                "risk_level": "medium",
                "blast_radius": "payment-svc only",
                "total_duration_ms": 5000,
            },
        )
        await notifier.on_event(final_event)

        mock_client.chat_update.assert_called_once()
        blocks = mock_client.chat_update.call_args.kwargs["blocks"]

        # Valida estrutura Block Kit
        block_types = [b["type"] for b in blocks]
        assert "header" in block_types
        assert "section" in block_types
        assert "divider" in block_types

        # Valida conteudo diagnostico
        full_text = str(blocks)
        assert "Connection pool exhausted" in full_text
        assert "Diagnosis Complete" in full_text

    @pytest.mark.asyncio
    async def test_final_message_clears_state(self):
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "777.888"}
        notifier = _make_notifier(mock_client)

        cid = "inc-20260308-test1234"
        await notifier.on_event(_make_event("pipeline.started"))
        await notifier.on_event(_make_event("pipeline.completed", data={"root_cause": "test"}))

        assert cid not in notifier._message_ts
        assert cid not in notifier._completed_nodes


class TestSlackApiFailureIsolation:
    """8.4: Falha no Slack nao afeta o pipeline."""

    @pytest.mark.asyncio
    async def test_slack_failure_does_not_raise(self):
        mock_client = AsyncMock()
        mock_client.chat_postMessage.side_effect = _make_slack_error("channel_not_found")
        notifier = _make_notifier(mock_client, max_retries=1)

        event = _make_event("pipeline.started")
        # Nao deve lancar excecao
        await notifier.on_event(event)

    @pytest.mark.asyncio
    async def test_unexpected_error_does_not_propagate(self):
        mock_client = AsyncMock()
        mock_client.chat_postMessage.side_effect = RuntimeError("network timeout")
        notifier = _make_notifier(mock_client, max_retries=1)

        event = _make_event("pipeline.started")
        await notifier.on_event(event)  # Nao deve lancar


class TestSlackNotifierDisabledMode:
    """8.5: SlackNotifier e ignorado quando slack.enabled=false."""

    @pytest.mark.asyncio
    async def test_no_client_means_no_op(self):
        notifier = SlackNotifier.__new__(SlackNotifier)
        notifier._client = None
        notifier._dry_run = False
        notifier._channel = "C12345"
        notifier._message_ts = {}
        notifier._channel_ids = {}
        notifier._completed_nodes = {}

        event = _make_event("pipeline.started")
        # Nao deve lancar nem tentar enviar
        await notifier.on_event(event)

    def test_disabled_config_creates_no_observer(self):
        from config.loader import AppConfig, NotificationsConfig, SlackConfig
        from interfaces.http.app import create_engine

        config = AppConfig(
            notifications=NotificationsConfig(
                slack=SlackConfig(enabled=False)
            )
        )
        engine = create_engine(config)
        assert len(engine._observers) == 0


class TestCredentialProvider:
    """8.6: Token resolvido via CredentialProvider, nunca hardcoded."""

    def test_token_from_env(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-token-12345")
        creds = EnvCredentialProvider()
        assert creds.get("SLACK_BOT_TOKEN") == "xoxb-test-token-12345"

    def test_missing_token_returns_none(self, monkeypatch):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        creds = EnvCredentialProvider()
        assert creds.get("SLACK_BOT_TOKEN") is None

    def test_notifier_without_token_has_no_client(self, monkeypatch):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        creds = EnvCredentialProvider()
        notifier = SlackNotifier(channel="C12345", creds=creds)
        assert notifier._client is None


class TestObserverRegistration:
    """8.7: Observer registrado no BlueprintEngine."""

    @pytest.mark.skip(reason="Aguarda Story 3.x: BlueprintEngine async com add_observer/add_node")
    @pytest.mark.asyncio
    async def test_observer_receives_events(self):
        from blueprint.engine import BlueprintEngine, BlueprintNode

        events_received = []

        class TestObserver:
            async def on_event(self, event: LifecycleEvent) -> None:
                events_received.append(event.event_type)

        engine = BlueprintEngine()
        engine.add_observer(TestObserver())

        await engine.run({"correlation_id": "test-123"})

        assert "pipeline.started" in events_received
        assert "pipeline.completed" in events_received

    @pytest.mark.asyncio
    async def test_observer_registered_when_slack_enabled(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake-token")
        from config.loader import AppConfig, NotificationsConfig, SlackConfig
        from interfaces.http.app import create_engine

        config = AppConfig(
            notifications=NotificationsConfig(
                slack=SlackConfig(enabled=True, channel="C12345")
            )
        )
        engine = create_engine(config)
        assert len(engine._observers) == 1
        assert isinstance(engine._observers[0], SlackNotifier)


# ---------------------------------------------------------------------------
# Story 3.2 — Tests 5.1-5.7
# ---------------------------------------------------------------------------

class TestRetryLogic:
    """5.1-5.7: Logica de retry com backoff exponencial."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt_no_retry(self):
        """5.1: Entrega bem sucedida na primeira tentativa — sem retry."""
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "1.1"}
        notifier = _make_notifier(mock_client)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await notifier.on_event(_make_event("pipeline.started"))
            mock_sleep.assert_not_called()

        assert mock_client.chat_postMessage.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        """5.2: Retry funciona na segunda tentativa com backoff de 1s."""
        mock_client = AsyncMock()
        mock_client.chat_postMessage.side_effect = [
            _make_slack_error("rate_limited"),
            {"ts": "123.456"},
        ]
        notifier = _make_notifier(mock_client, max_retries=3, initial_backoff_s=1.0)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await notifier.on_event(_make_event("pipeline.started"))
            mock_sleep.assert_called_once_with(1.0)  # Primeiro backoff = 1s

        assert mock_client.chat_postMessage.call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_writes_fallback(self, tmp_path):
        """5.3: Fallback JSON gravado apos esgotar todos os retries."""
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "ts.init"}
        mock_client.chat_update.side_effect = _make_slack_error("channel_not_found")
        notifier = _make_notifier(mock_client, max_retries=3, fallback_dir=str(tmp_path))

        cid = "inc-20260308-test1234"
        await notifier.on_event(_make_event("pipeline.started"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await notifier.on_event(_make_event(
                "pipeline.completed",
                data={"root_cause": "DB timeout"},
            ))

        fallback_files = list(tmp_path.glob("fallback_*.json"))
        assert len(fallback_files) == 1

    @pytest.mark.asyncio
    async def test_fallback_json_contains_complete_data(self, tmp_path):
        """5.4: JSON de fallback contem postmortem completo."""
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "ts.init"}
        mock_client.chat_update.side_effect = _make_slack_error("channel_not_found")
        notifier = _make_notifier(mock_client, max_retries=1, fallback_dir=str(tmp_path))

        postmortem_data = {
            "root_cause": "Connection pool exhausted",
            "confidence": 0.95,
            "proposed_fix": "kubectl rollout restart",
        }
        cid = "inc-20260308-test1234"
        await notifier.on_event(_make_event("pipeline.started"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await notifier.on_event(_make_event("pipeline.completed", data=postmortem_data))

        fallback_files = list(tmp_path.glob("fallback_*.json"))
        assert len(fallback_files) == 1
        data = json.loads(fallback_files[0].read_text())
        assert data["correlation_id"] == cid
        assert data["fallback_reason"] == "slack_delivery_exhausted"
        assert "postmortem" in data

    @pytest.mark.asyncio
    async def test_auth_error_not_retried(self, tmp_path):
        """5.6: Erros de autenticacao nao sao retentados."""
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "ts.init"}
        mock_client.chat_update.side_effect = _make_slack_error("invalid_auth")
        notifier = _make_notifier(mock_client, max_retries=3, fallback_dir=str(tmp_path))

        await notifier.on_event(_make_event("pipeline.started"))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await notifier.on_event(_make_event("pipeline.completed", data={"root_cause": "test"}))
            mock_sleep.assert_not_called()  # Sem retry para erro de auth

        # chat_update chamado apenas 1 vez (sem retry)
        assert mock_client.chat_update.call_count == 1

    @pytest.mark.asyncio
    async def test_configurable_retry_settings(self):
        """5.7: Configuracoes de retry sao respeitadas."""
        mock_client = AsyncMock()
        mock_client.chat_postMessage.side_effect = [
            _make_slack_error("rate_limited"),
            _make_slack_error("rate_limited"),
            {"ts": "final.ts"},
        ]
        notifier = _make_notifier(mock_client, max_retries=5, initial_backoff_s=0.5)
        notifier._backoff_multiplier = 3.0

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await notifier.on_event(_make_event("pipeline.started"))
            # Dois retries: 0.5s e 1.5s (0.5 * 3^1)
            assert mock_sleep.call_count == 2
            mock_sleep.assert_any_call(0.5)
            mock_sleep.assert_any_call(1.5)


# ---------------------------------------------------------------------------
# Story 3.2 — LocalFallbackWriter
# ---------------------------------------------------------------------------

class TestLocalFallbackWriter:
    """Testes para LocalFallbackWriter."""

    def test_write_creates_json_file(self, tmp_path):
        writer = LocalFallbackWriter(str(tmp_path))
        filepath = writer.write("inc-test-123", {"root_cause": "DB timeout"})

        assert filepath.exists()
        assert filepath.name.startswith("fallback_inc-test-123_")
        assert filepath.suffix == ".json"

    def test_write_json_structure(self, tmp_path):
        writer = LocalFallbackWriter(str(tmp_path))
        writer.write("inc-test-456", {"confidence": 0.8, "evidence": ["err1", "err2"]})

        files = list(tmp_path.glob("fallback_*.json"))
        data = json.loads(files[0].read_text())
        assert data["correlation_id"] == "inc-test-456"
        assert data["fallback_reason"] == "slack_delivery_exhausted"
        assert "written_at" in data
        assert data["postmortem"]["confidence"] == 0.8

    def test_creates_output_dir_if_missing(self, tmp_path):
        new_dir = tmp_path / "subdir" / "output"
        writer = LocalFallbackWriter(str(new_dir))
        writer.write("inc-dir-test", {"root_cause": "test"})
        assert new_dir.exists()


# ---------------------------------------------------------------------------
# Story 3.6 — Config YAML Tests (8.1-8.7)
# ---------------------------------------------------------------------------

class TestSlackConfig:
    """8.1: SlackConfig carrega do YAML com todos os campos."""

    def test_slack_config_defaults(self):
        from config.loader import SlackConfig
        config = SlackConfig()
        assert config.enabled is False
        assert config.max_retries == 3
        assert config.initial_backoff_s == 1.0
        assert config.backoff_multiplier == 2.0
        assert config.fallback_dir == "output"
        assert config.dry_run is False

    def test_slack_enabled_requires_channel(self):
        from pydantic import ValidationError
        from config.loader import SlackConfig
        with pytest.raises(ValidationError, match="channel is required"):
            SlackConfig(enabled=True, channel="")

    def test_slack_negative_retries_rejected(self):
        from pydantic import ValidationError
        from config.loader import SlackConfig
        with pytest.raises(ValidationError, match="must be >= 0"):
            SlackConfig(max_retries=-1)

    def test_slack_zero_backoff_rejected(self):
        from pydantic import ValidationError
        from config.loader import SlackConfig
        with pytest.raises(ValidationError, match="must be > 0"):
            SlackConfig(initial_backoff_s=0.0)


class TestObservabilityConfig:
    """8.6: ObservabilityConfig carrega com defaults corretos."""

    def test_observability_config_defaults(self):
        from config.loader import ObservabilityConfig
        config = ObservabilityConfig()
        assert config.metric_retention_hours == 24
        assert config.accuracy_tracking_enabled is True

    def test_observability_invalid_retention(self):
        from pydantic import ValidationError
        from config.loader import ObservabilityConfig
        with pytest.raises(ValidationError, match="must be >= 1"):
            ObservabilityConfig(metric_retention_hours=0)


class TestEnvVarOverride:
    """8.2: Env var sobrescreve YAML."""

    def test_env_var_overrides_channel(self, monkeypatch, tmp_path):
        yaml_content = """
notifications:
  slack:
    enabled: true
    channel: "C_FROM_YAML"
"""
        yaml_file = tmp_path / "test_config.yaml"
        yaml_file.write_text(yaml_content)

        monkeypatch.setenv("AIOPS_NOTIFICATIONS__SLACK__CHANNEL", "C_FROM_ENV")

        from config.loader import load_config
        config = load_config(yaml_file=yaml_file)
        assert config.notifications.slack.channel == "C_FROM_ENV"

    def test_env_var_enables_slack(self, monkeypatch, tmp_path):
        yaml_content = "notifications:\n  slack:\n    enabled: false\n    channel: C12345\n"
        yaml_file = tmp_path / "test_config.yaml"
        yaml_file.write_text(yaml_content)

        monkeypatch.setenv("AIOPS_NOTIFICATIONS__SLACK__ENABLED", "true")

        from config.loader import load_config
        config = load_config(yaml_file=yaml_file)
        assert config.notifications.slack.enabled is True


class TestDryRunMode:
    """8.5: Dry-run loga mas nao chama Slack API."""

    def test_dry_run_creates_no_client(self, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-real-token")
        creds = EnvCredentialProvider()
        notifier = SlackNotifier(channel="C12345", creds=creds, dry_run=True)
        assert notifier._client is None
        assert notifier._dry_run is True

    @pytest.mark.asyncio
    async def test_dry_run_does_not_call_slack_api(self):
        mock_client = AsyncMock()
        notifier = SlackNotifier.__new__(SlackNotifier)
        notifier._client = None  # dry_run nao inicializa client
        notifier._channel = "C12345"
        notifier._dry_run = True
        notifier._message_ts = {}
        notifier._channel_ids = {}
        notifier._completed_nodes = {}
        notifier._max_retries = 3
        notifier._initial_backoff_s = 1.0
        notifier._backoff_multiplier = 2.0
        from pathlib import Path
        notifier._fallback_dir = Path("output")

        await notifier.on_event(_make_event("pipeline.started"))
        await notifier.on_event(_make_event("pipeline.completed", data={"root_cause": "test"}))

        mock_client.chat_postMessage.assert_not_called()
        mock_client.chat_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_sets_fake_ts(self):
        notifier = SlackNotifier.__new__(SlackNotifier)
        notifier._client = None
        notifier._channel = "C12345"
        notifier._dry_run = True
        notifier._message_ts = {}
        notifier._channel_ids = {}
        notifier._completed_nodes = {}
        notifier._max_retries = 3
        notifier._initial_backoff_s = 1.0
        notifier._backoff_multiplier = 2.0
        from pathlib import Path
        notifier._fallback_dir = Path("output")

        cid = "inc-20260308-test1234"
        await notifier.on_event(_make_event("pipeline.started"))
        assert notifier._message_ts[cid] == "dry-run"


class TestDisabledNotifications:
    """8.7: Notificacoes desabilitadas nao criam SlackNotifier."""

    def test_disabled_slack_no_observer(self):
        from config.loader import AppConfig, NotificationsConfig, SlackConfig
        from interfaces.http.app import create_engine

        config = AppConfig(
            notifications=NotificationsConfig(slack=SlackConfig(enabled=False))
        )
        engine = create_engine(config)
        assert len(engine._observers) == 0


# ---------------------------------------------------------------------------
# BlueprintEngine observer tests
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="Aguarda Story 3.x: BlueprintEngine async com add_observer/add_node/run(dict)")
class TestBlueprintEngineObserver:
    """Testes do mecanismo de observer do BlueprintEngine."""

    @pytest.mark.asyncio
    async def test_observer_failure_does_not_abort_pipeline(self):
        from blueprint.engine import BlueprintEngine, BlueprintNode

        class FailingObserver:
            async def on_event(self, event: LifecycleEvent) -> None:
                raise RuntimeError("Observer crashed!")

        class SimpleNode(BlueprintNode):
            async def execute(self, state: dict) -> dict:
                return {**state, "processed": True}

        engine = BlueprintEngine()
        engine.add_observer(FailingObserver())
        engine.add_node(SimpleNode("test_node"))

        # Pipeline nao deve crashar mesmo com observer falhando
        result = await engine.run({"correlation_id": "test-crash"})
        assert result["processed"] is True

    @pytest.mark.asyncio
    async def test_node_events_emitted_in_order(self):
        from blueprint.engine import BlueprintEngine, BlueprintNode

        received = []

        class RecordingObserver:
            async def on_event(self, event: LifecycleEvent) -> None:
                received.append((event.event_type, event.node_name))

        class NodeA(BlueprintNode):
            async def execute(self, state: dict) -> dict:
                return state

        class NodeB(BlueprintNode):
            async def execute(self, state: dict) -> dict:
                return state

        engine = BlueprintEngine()
        engine.add_observer(RecordingObserver())
        engine.add_node(NodeA("node_a"))
        engine.add_node(NodeB("node_b"))

        await engine.run({"correlation_id": "order-test"})

        event_types = [e[0] for e in received]
        assert event_types[0] == "pipeline.started"
        assert "node.started" in event_types
        assert "node.completed" in event_types
        assert event_types[-1] == "pipeline.completed"

    @pytest.mark.asyncio
    async def test_node_failure_emits_node_failed_event(self):
        from blueprint.engine import BlueprintEngine, BlueprintNode

        failed_events = []

        class RecordingObserver:
            async def on_event(self, event: LifecycleEvent) -> None:
                if event.event_type in ("node.failed", "pipeline.failed"):
                    failed_events.append(event)

        class FailingNode(BlueprintNode):
            async def execute(self, state: dict) -> dict:
                raise ValueError("Node execution failed")

        engine = BlueprintEngine()
        engine.add_observer(RecordingObserver())
        engine.add_node(FailingNode("bad_node"))

        with pytest.raises(ValueError):
            await engine.run({"correlation_id": "fail-test"})

        assert any(e.event_type == "node.failed" for e in failed_events)
        assert any(e.event_type == "pipeline.failed" for e in failed_events)

    @pytest.mark.asyncio
    async def test_multiple_observers_all_receive_events(self):
        from blueprint.engine import BlueprintEngine

        received_a: list[str] = []
        received_b: list[str] = []

        class ObserverA:
            async def on_event(self, event: LifecycleEvent) -> None:
                received_a.append(event.event_type)

        class ObserverB:
            async def on_event(self, event: LifecycleEvent) -> None:
                received_b.append(event.event_type)

        engine = BlueprintEngine()
        engine.add_observer(ObserverA())
        engine.add_observer(ObserverB())

        await engine.run({"correlation_id": "multi-test"})

        assert "pipeline.started" in received_a
        assert "pipeline.started" in received_b
        assert "pipeline.completed" in received_a
        assert "pipeline.completed" in received_b


# ---------------------------------------------------------------------------
# LifecycleObserver Protocol compliance
# ---------------------------------------------------------------------------

class TestLifecycleObserverProtocol:
    """Verifica que SlackNotifier satisfaz o protocolo LifecycleObserver."""

    def test_slack_notifier_satisfies_protocol(self, monkeypatch):
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        creds = EnvCredentialProvider()
        notifier = SlackNotifier(channel="C12345", creds=creds)
        assert isinstance(notifier, LifecycleObserver)
