"""
Testes para rate limiter MCP (Story 5.5).

Verifica sliding window, isolamento por cliente, e integração com config.
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
class TestRateLimitResult:
    """Testes para o dataclass RateLimitResult."""

    def test_allowed_result(self):
        """Verifica campos de resultado permitido."""
        from interfaces.mcp.rate_limiter import RateLimitResult

        r = RateLimitResult(allowed=True, remaining=9, retry_after=0.0, limit=10)
        assert r.allowed is True
        assert r.remaining == 9
        assert r.retry_after == 0.0
        assert r.limit == 10

    def test_denied_result(self):
        """Verifica campos de resultado negado."""
        from interfaces.mcp.rate_limiter import RateLimitResult

        r = RateLimitResult(allowed=False, remaining=0, retry_after=42.3, limit=10)
        assert r.allowed is False
        assert r.remaining == 0
        assert r.retry_after == 42.3


@pytest.mark.unit
class TestRateLimiterProtocol:
    """Verifica que InMemoryRateLimiter implementa o protocolo."""

    def test_implements_protocol(self):
        """Verifica isinstance check via @runtime_checkable."""
        from interfaces.mcp.rate_limiter import InMemoryRateLimiter, RateLimiter

        limiter = InMemoryRateLimiter()
        assert isinstance(limiter, RateLimiter)


@pytest.mark.unit
class TestInMemoryRateLimiter:
    """Testes para InMemoryRateLimiter."""

    def test_calls_within_limit_allowed(self):
        """Verifica que chamadas dentro do limite são permitidas."""
        from interfaces.mcp.rate_limiter import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(limits={"diagnose_incident": 3})

        r1 = limiter.check("client-a", "diagnose_incident")
        assert r1.allowed is True
        assert r1.remaining == 2
        assert r1.limit == 3

        r2 = limiter.check("client-a", "diagnose_incident")
        assert r2.allowed is True
        assert r2.remaining == 1

        r3 = limiter.check("client-a", "diagnose_incident")
        assert r3.allowed is True
        assert r3.remaining == 0

    def test_call_exceeding_limit_denied(self, monkeypatch):
        """Verifica que chamada além do limite retorna allowed=False com retry_after > 0."""
        current_time = 1000.0
        monkeypatch.setattr("time.monotonic", lambda: current_time)

        from interfaces.mcp.rate_limiter import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(limits={"diagnose_incident": 2})

        r1 = limiter.check("client-a", "diagnose_incident")
        assert r1.allowed is True
        assert r1.remaining == 1

        r2 = limiter.check("client-a", "diagnose_incident")
        assert r2.allowed is True
        assert r2.remaining == 0

        # Terceira chamada — excede limite
        r3 = limiter.check("client-a", "diagnose_incident")
        assert r3.allowed is False
        assert r3.remaining == 0
        assert r3.retry_after > 0

    def test_per_client_isolation(self, monkeypatch):
        """Verifica que limite de client A não afeta client B."""
        current_time = 1000.0
        monkeypatch.setattr("time.monotonic", lambda: current_time)

        from interfaces.mcp.rate_limiter import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(limits={"diagnose_incident": 2})

        # Esgota limite do client A
        limiter.check("client-a", "diagnose_incident")
        limiter.check("client-a", "diagnose_incident")
        r_a = limiter.check("client-a", "diagnose_incident")
        assert r_a.allowed is False

        # Client B ainda pode chamar
        r_b = limiter.check("client-b", "diagnose_incident")
        assert r_b.allowed is True

    def test_expired_entries_evicted(self, monkeypatch):
        """Verifica que entradas expiradas são evicadas após 60 segundos."""
        current_time = 1000.0
        monkeypatch.setattr("time.monotonic", lambda: current_time)

        from interfaces.mcp.rate_limiter import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(limits={"diagnose_incident": 2})

        # Esgota limite no t=1000
        limiter.check("client-a", "diagnose_incident")
        limiter.check("client-a", "diagnose_incident")
        blocked = limiter.check("client-a", "diagnose_incident")
        assert blocked.allowed is False

        # Avança tempo para além da janela (>60s)
        current_time = 1061.0
        monkeypatch.setattr("time.monotonic", lambda: current_time)

        # Agora deve ser permitido novamente
        r = limiter.check("client-a", "diagnose_incident")
        assert r.allowed is True

    def test_different_tools_have_different_limits(self):
        """Verifica que diferentes ferramentas têm limites diferentes."""
        from interfaces.mcp.rate_limiter import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(
            limits={"diagnose_incident": 2, "search_incidents": 5}
        )

        # Esgota diagnose (limite=2)
        limiter.check("client-a", "diagnose_incident")
        limiter.check("client-a", "diagnose_incident")
        blocked_diagnose = limiter.check("client-a", "diagnose_incident")
        assert blocked_diagnose.allowed is False

        # Search ainda tem slots (limite=5)
        r_search = limiter.check("client-a", "search_incidents")
        assert r_search.allowed is True

    def test_from_config(self):
        """Verifica criação a partir de McpRateLimitsConfig."""
        from config.loader import McpRateLimitsConfig
        from interfaces.mcp.rate_limiter import InMemoryRateLimiter

        rate_cfg = McpRateLimitsConfig(diagnose=5, search=30, validate=15)
        limiter = InMemoryRateLimiter.from_config(rate_cfg)

        # Verifica que os limites foram aplicados
        # Esgota 5 chamadas de diagnose
        for _ in range(5):
            r = limiter.check("test-client", "diagnose_incident")
            assert r.allowed is True

        blocked = limiter.check("test-client", "diagnose_incident")
        assert blocked.allowed is False
        assert blocked.limit == 5

    def test_unknown_tool_uses_default_limit(self):
        """Verifica que ferramenta desconhecida usa limite default (30)."""
        from interfaces.mcp.rate_limiter import InMemoryRateLimiter

        limiter = InMemoryRateLimiter()
        r = limiter.check("client-a", "unknown_tool")
        assert r.allowed is True
        assert r.limit == 30

    def test_retry_after_calculated_correctly(self, monkeypatch):
        """Verifica que retry_after é calculado a partir do timestamp mais antigo."""
        current_time = 1000.0
        monkeypatch.setattr("time.monotonic", lambda: current_time)

        from interfaces.mcp.rate_limiter import InMemoryRateLimiter

        limiter = InMemoryRateLimiter(limits={"diagnose_incident": 1})

        # Uma chamada — esgota limite
        limiter.check("client-a", "diagnose_incident")

        # Avança 30 segundos (metade da janela)
        current_time = 1030.0
        monkeypatch.setattr("time.monotonic", lambda: current_time)

        r = limiter.check("client-a", "diagnose_incident")
        assert r.allowed is False
        # retry_after ≈ 60 - 30 = 30 segundos
        assert 29.0 <= r.retry_after <= 31.0
