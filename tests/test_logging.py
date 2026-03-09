"""Testes para core/logging.py — Story 1.3."""

from __future__ import annotations

import json
import logging

import structlog
from structlog.testing import capture_logs

from core.logging import (
    _redact_secrets,
    bind_context,
    configure_logging,
    get_logger,
)


class TestConfigureLogging:
    """Testes de configuracao do logging."""

    def test_produces_json_output(self):
        """configure_logging deve configurar JSON formatter no root logger."""
        configure_logging()
        root = logging.getLogger()
        assert len(root.handlers) > 0
        handler = root.handlers[0]
        formatter = handler.formatter
        # Formatter deve ser ProcessorFormatter do structlog
        assert isinstance(formatter, structlog.stdlib.ProcessorFormatter)

    def test_respects_log_level_env(self, monkeypatch):
        """Deve respeitar AIOPS_LOG_LEVEL do ambiente."""
        monkeypatch.setenv("AIOPS_LOG_LEVEL", "DEBUG")
        configure_logging()
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_default_level_is_info(self):
        """Nivel padrao deve ser INFO."""
        configure_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_stdlib_routes_through_structlog(self):
        """Logs do stdlib devem rotear pelo structlog."""
        configure_logging()
        root = logging.getLogger()
        handler = root.handlers[0]
        # O handler deve ter um formatter do structlog
        assert hasattr(handler.formatter, 'processors')


class TestGetLogger:
    """Testes para get_logger."""

    def test_returns_named_logger(self):
        """get_logger deve retornar logger com nome correto."""
        configure_logging()
        log = get_logger("pipeline")
        # Logger deve ser utilizavel
        with capture_logs() as cap_logs:
            log.info("pipeline.test_event")
        assert len(cap_logs) == 1
        assert cap_logs[0]["event"] == "pipeline.test_event"


class TestBindContext:
    """Testes para bind_context."""

    def test_includes_all_fields(self):
        """bind_context deve incluir correlation_id, pipeline e node nos logs."""
        configure_logging()
        with capture_logs() as cap_logs:
            log = bind_context(
                correlation_id="inc-20260308-abc12345",
                pipeline="incident_response",
                node="log_parser",
            )
            log.info("node.started", input_lines=500)

        assert len(cap_logs) == 1
        entry = cap_logs[0]
        assert entry["correlation_id"] == "inc-20260308-abc12345"
        assert entry["pipeline"] == "incident_response"
        assert entry["node"] == "log_parser"
        assert entry["input_lines"] == 500
        assert entry["event"] == "node.started"

    def test_partial_binding(self):
        """bind_context com campos parciais deve funcionar."""
        configure_logging()
        with capture_logs() as cap_logs:
            log = bind_context(correlation_id="inc-20260308-abc12345")
            log.info("pipeline.started")

        entry = cap_logs[0]
        assert entry["correlation_id"] == "inc-20260308-abc12345"
        assert "pipeline" not in entry
        assert "node" not in entry


class TestSecretRedaction:
    """Testes para redacao de secrets — testados via processador direto."""

    def test_redacts_anthropic_api_key(self):
        """Deve redactar padroes de API key Anthropic."""
        event_dict = {
            "event": "config.loaded",
            "detail": "Using key sk-ant-abcdefghijklmnopqrst123",
        }
        result = _redact_secrets(None, "info", event_dict)
        assert "[REDACTED]" in result["detail"]
        assert "sk-ant-" not in result["detail"]

    def test_redacts_openai_style_key(self):
        """Deve redactar padroes de API key OpenAI."""
        event_dict = {
            "event": "config.loaded",
            "detail": "key is sk-abcdefghijklmnopqrstuvwxyz123456",
        }
        result = _redact_secrets(None, "info", event_dict)
        assert "[REDACTED]" in result["detail"]

    def test_redacts_by_key_name(self):
        """Deve redactar valores de chaves sensiveis independente do conteudo."""
        event_dict = {
            "event": "auth.configured",
            "api_key": "some-random-value",
            "password": "hunter2",
        }
        result = _redact_secrets(None, "info", event_dict)
        assert result["api_key"] == "[REDACTED]"
        assert result["password"] == "[REDACTED]"

    def test_redacts_token_key(self):
        """Deve redactar chave 'token'."""
        event_dict = {"event": "auth.token", "token": "my-secret-token"}
        result = _redact_secrets(None, "info", event_dict)
        assert result["token"] == "[REDACTED]"

    def test_redacts_bearer_token(self):
        """Deve redactar Bearer tokens."""
        event_dict = {
            "event": "auth.header",
            "header": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test",
        }
        result = _redact_secrets(None, "info", event_dict)
        assert "Bearer" not in result["header"] or "[REDACTED]" in result["header"]

    def test_redacts_slack_token(self):
        """Deve redactar Slack bot tokens."""
        event_dict = {
            "event": "slack.configured",
            "detail": "Using xoxb-123456-789012-abcdef",
        }
        result = _redact_secrets(None, "info", event_dict)
        assert "xoxb-" not in result["detail"]

    def test_preserves_non_secret_values(self):
        """Valores nao-secretos devem ser preservados."""
        event_dict = {
            "event": "pipeline.completed",
            "duration_ms": 1234,
            "nodes": 7,
            "message": "all good",
        }
        result = _redact_secrets(None, "info", event_dict)
        assert result["duration_ms"] == 1234
        assert result["nodes"] == 7
        assert result["message"] == "all good"

    def test_redacts_multiple_patterns_in_one_value(self):
        """Deve redactar multiplos padroes no mesmo valor."""
        event_dict = {
            "event": "test",
            "detail": "keys: sk-ant-abcdefghijklmnopqrst123 and sk-abcdefghijklmnopqrstuvwxyz123456",
        }
        result = _redact_secrets(None, "info", event_dict)
        assert "sk-ant-" not in result["detail"]
        assert "sk-" not in result["detail"] or result["detail"] == "[REDACTED] and [REDACTED]"
