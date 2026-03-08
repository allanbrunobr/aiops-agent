"""Tests de segurança para API keys — Story 3.3.

Valida:
- Utilitários de sanitização redact padrões de API key corretamente (AC #1)
- Output de pipeline não contém padrões de API key (AC #2)
- Postmortem gerado não contém padrões de API key (AC #2, #4)
- Template de postmortem não tem interpolação de env vars (AC #4)
- Missing API key produz fallback limpo sem stack trace (AC #5)
- Mensagens de erro do diagnoser não expõem nomes de env vars (AC #3)
"""
from __future__ import annotations

import inspect
import io
import os
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import pytest


# ──────────────────────────────────────────────────────────────────────
# Task 4.2-4.5: Unit tests for sanitization utilities (AC #1, #3)
# ──────────────────────────────────────────────────────────────────────

FAKE_ANTHROPIC_KEY = "sk-ant-api03-FAKE1234567890abcdefghijklmnop1234567890abcdef"
FAKE_DAPI_KEY = "dapiFAKE1234567890abcdefghijklmnop"
FAKE_OPENAI_KEY = "sk-FAKE1234567890abcdefghijklmnop1234567890abcdef"
FAKE_GENERIC_KEY = "key-FAKE1234567890abcdefghijklmnop1234567890abcdef"


@pytest.mark.unit
class TestSanitizeSecrets:
    """Testes unitários para _sanitize_secrets()."""

    def test_sanitize_secrets_redacts_anthropic_key(self) -> None:
        """_sanitize_secrets() deve substituir padrões sk-ant-* por [REDACTED]."""
        from main import _sanitize_secrets

        text = f"Error: Invalid API key {FAKE_ANTHROPIC_KEY} in request"
        result = _sanitize_secrets(text)
        assert FAKE_ANTHROPIC_KEY not in result
        assert "[REDACTED]" in result
        assert "Error: Invalid API key" in result

    def test_sanitize_secrets_redacts_dapi_key(self) -> None:
        """_sanitize_secrets() deve substituir padrões dapi* por [REDACTED]."""
        from main import _sanitize_secrets

        text = f"Token: {FAKE_DAPI_KEY}"
        result = _sanitize_secrets(text)
        assert FAKE_DAPI_KEY not in result
        assert "[REDACTED]" in result

    def test_sanitize_secrets_redacts_openai_key(self) -> None:
        """_sanitize_secrets() deve substituir padrões sk-* (32+ chars) por [REDACTED]."""
        from main import _sanitize_secrets

        text = f"Key used: {FAKE_OPENAI_KEY}"
        result = _sanitize_secrets(text)
        assert FAKE_OPENAI_KEY not in result
        assert "[REDACTED]" in result

    def test_sanitize_secrets_preserves_normal_text(self) -> None:
        """_sanitize_secrets() NÃO deve modificar texto normal de pipeline."""
        from main import _sanitize_secrets

        normal_texts = [
            "Pipeline completed successfully",
            "payment-svc connection pool exhaustion detected",
            "Root cause: config change reduced pool from 200 to 50",
            "Postmortem saved to: output/postmortem_2026-03-06_143000.md",
            "Exit code: 0 (resolved)",
            "sk-short",  # Muito curto para ser uma API key
            "dapi",  # Muito curto para ser uma API key
        ]
        for text in normal_texts:
            assert _sanitize_secrets(text) == text, (
                f"_sanitize_secrets() modificou texto normal: '{text}'"
            )


@pytest.mark.unit
class TestContainsApiKeyPattern:
    """Testes unitários para _contains_api_key_pattern()."""

    def test_detects_anthropic_key(self) -> None:
        """Deve detectar padrão sk-ant-*."""
        from main import _contains_api_key_pattern

        assert _contains_api_key_pattern(FAKE_ANTHROPIC_KEY) is True

    def test_detects_dapi_key(self) -> None:
        """Deve detectar padrão dapi*."""
        from main import _contains_api_key_pattern

        assert _contains_api_key_pattern(FAKE_DAPI_KEY) is True

    def test_does_not_detect_normal_text(self) -> None:
        """Não deve detectar texto normal."""
        from main import _contains_api_key_pattern

        assert _contains_api_key_pattern("Pipeline completed successfully") is False
        assert _contains_api_key_pattern("payment-svc error") is False
        assert _contains_api_key_pattern("sk-short") is False


# ──────────────────────────────────────────────────────────────────────
# Task 4.6: Pipeline output scan (AC #2)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestPipelineOutputNoApiKeys:
    """Testes de integração: output do pipeline não contém API keys."""

    def test_pipeline_output_contains_no_api_keys(self) -> None:
        """Output do pipeline com fake API key em env NÃO deve conter a key."""
        from main import _contains_api_key_pattern, main

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": FAKE_ANTHROPIC_KEY}), \
             patch("sys.argv", ["main.py", "--no-llm", "--no-rag"]), \
             redirect_stdout(stdout_buf), \
             redirect_stderr(stderr_buf):
            main()

        all_output = stdout_buf.getvalue() + stderr_buf.getvalue()
        assert not _contains_api_key_pattern(all_output), (
            f"Output do pipeline contém padrão de API key!"
        )
        assert FAKE_ANTHROPIC_KEY not in all_output, (
            f"Output do pipeline contém a fake API key literal!"
        )


# ──────────────────────────────────────────────────────────────────────
# Task 4.7-4.8: Postmortem audit (AC #2, #4)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestPostmortemNoApiKeys:
    """Testes de integração: postmortem não contém API keys."""

    def test_postmortem_contains_no_api_keys(self, pipeline_result, simulated_incident) -> None:
        """Postmortem gerado NÃO deve conter padrões de API key."""
        from main import step_postmortem, _contains_api_key_pattern

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            path, _ = step_postmortem(pipeline_result, None, simulated_incident)

        text = path.read_text(encoding="utf-8")
        path.unlink(missing_ok=True)

        assert not _contains_api_key_pattern(text), (
            "Postmortem contém padrão de API key!"
        )

    def test_postmortem_template_has_no_env_interpolation(self) -> None:
        """Source code de step_postmortem() NÃO deve referenciar api_key ou os.environ."""
        from main import step_postmortem

        source = inspect.getsource(step_postmortem)
        forbidden_patterns = ["os.environ", "api_key", "ANTHROPIC_API_KEY", "API_KEY"]
        for pattern in forbidden_patterns:
            assert pattern not in source, (
                f"step_postmortem() contém referência a '{pattern}' — risco de key interpolation"
            )


# ──────────────────────────────────────────────────────────────────────
# Task 4.9: Missing API key fallback (AC #5)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestMissingApiKeyFallback:
    """Testes de integração: missing API key produz fallback limpo."""

    def test_missing_api_key_produces_clean_fallback(self) -> None:
        """Pipeline sem API key roda em modo heurístico sem stack trace."""
        from main import main

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()

        # Garante que ANTHROPIC_API_KEY NÃO está definida
        env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True), \
             patch("sys.argv", ["main.py", "--no-llm", "--no-rag"]), \
             redirect_stdout(stdout_buf), \
             redirect_stderr(stderr_buf):
            exit_code = main()

        all_output = stdout_buf.getvalue() + stderr_buf.getvalue()

        # Deve completar sem crash (exit code 0 ou 3, não 1)
        assert exit_code != 1, (
            f"Pipeline crashou com exit code 1 (exception). Output:\n{all_output[:500]}"
        )
        # AC #5: fallback limpo — sem stack trace E sem nome de env var
        assert "Traceback" not in all_output, (
            f"Fallback produziu stack trace em vez de mensagem limpa:\n{all_output[:500]}"
        )
        assert "ANTHROPIC_API_KEY" not in all_output, (
            "Output expôs nome de variável de ambiente ANTHROPIC_API_KEY"
        )


# ──────────────────────────────────────────────────────────────────────
# Task 4.10: Diagnoser error message audit (AC #3)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestDiagnoserErrorMessages:
    """Testes unitários: mensagens de erro do diagnoser não expõem env vars."""

    def test_diagnoser_error_message_no_env_var_name(self) -> None:
        """_get_client() deve lançar ValueError sem mencionar ANTHROPIC_API_KEY."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent(max_iterations=1)

        # Garante que ANTHROPIC_API_KEY NÃO está definida
        env_without_key = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True):
            with pytest.raises(ValueError) as exc_info:
                agent._get_client()

        error_msg = str(exc_info.value)
        assert "ANTHROPIC_API_KEY" not in error_msg, (
            f"ValueError expõe nome de env var: '{error_msg}'"
        )
        assert "api key" in error_msg.lower() or "not configured" in error_msg.lower(), (
            f"ValueError deve mencionar 'API key' genericamente: '{error_msg}'"
        )
