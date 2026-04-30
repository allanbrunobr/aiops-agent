"""
Rate limiter por cliente e por ferramenta MCP.

Implementa sliding window de 60 segundos para limitar chamadas de ferramentas.
Baseado em decisão de arquitetura: limites assimétricos por custo da ferramenta.

Referência: architecture.md, Lines 311-321.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class RateLimitResult:
    """Resultado de verificação de rate limit.

    Retornado por RateLimiter.check() para cada chamada de ferramenta.
    """

    allowed: bool
    remaining: int
    retry_after: float  # Segundos até próximo slot (0.0 se permitido)
    limit: int


@runtime_checkable
class RateLimiter(Protocol):
    """Protocolo para rate limiting de ferramentas MCP.

    V1: InMemoryRateLimiter (sliding window em memória).
    V2 path: RedisRateLimiter para estado distribuído.
    """

    def check(self, client_id: str, tool_name: str) -> RateLimitResult:
        """Verifica se chamada está dentro do limite de taxa.

        Args:
            client_id: Identificador do cliente MCP.
            tool_name: Nome da ferramenta sendo invocada.

        Returns:
            RateLimitResult com decisão e metadata.
        """
        ...


class InMemoryRateLimiter:
    """Rate limiter em memória com sliding window de 60 segundos.

    Rastreia chamadas por (client_id, tool_name) usando timestamps.
    Entradas expiradas são removidas a cada verificação.
    Estado resetado no restart do servidor (aceitável para V1).
    """

    # Janela de tempo padrão: 60 segundos
    WINDOW_SECONDS: float = 60.0

    def __init__(self, limits: dict[str, int] | None = None) -> None:
        """Inicializa rate limiter com limites por ferramenta.

        Args:
            limits: Mapeamento tool_name -> max_calls_per_minute.
                    Se None, usa defaults da arquitetura.
        """
        self._limits: dict[str, int] = limits or {
            "diagnose_incident": 10,
            "search_incidents": 60,
            "validate_action": 30,
        }
        self._default_limit: int = 30
        # Chave: (client_id, tool_name) -> lista de timestamps
        self._calls: dict[tuple[str, str], list[float]] = defaultdict(list)

    @classmethod
    def from_config(cls, rate_limits_config: object) -> InMemoryRateLimiter:
        """Cria instância a partir de McpRateLimitsConfig.

        Mapeia nomes curtos do config para nomes completos das ferramentas.

        Args:
            rate_limits_config: Objeto com campos diagnose, search, validate.

        Returns:
            InMemoryRateLimiter configurado com os limites do config.
        """
        limits = {
            "diagnose_incident": getattr(rate_limits_config, "diagnose", 10),
            "search_incidents": getattr(rate_limits_config, "search", 60),
            "validate_action": getattr(rate_limits_config, "validate", 30),
        }
        return cls(limits=limits)

    def check(self, client_id: str, tool_name: str) -> RateLimitResult:
        """Verifica se chamada está dentro do limite de taxa.

        Implementa sliding window: remove timestamps mais antigos que 60s,
        conta restantes, decide allow/deny.

        Args:
            client_id: Identificador do cliente MCP.
            tool_name: Nome da ferramenta sendo invocada.

        Returns:
            RateLimitResult com decisão, remaining e retry_after.
        """
        now = time.monotonic()
        key = (client_id, tool_name)
        limit = self._limits.get(tool_name, self._default_limit)

        # Evicta entradas expiradas
        cutoff = now - self.WINDOW_SECONDS
        self._calls[key] = [t for t in self._calls[key] if t > cutoff]

        current_count = len(self._calls[key])

        if current_count < limit:
            # Permitido — registra timestamp
            self._calls[key].append(now)
            return RateLimitResult(
                allowed=True,
                remaining=limit - current_count - 1,
                retry_after=0.0,
                limit=limit,
            )

        # Excedido — calcula retry_after baseado no timestamp mais antigo
        oldest = self._calls[key][0] if self._calls[key] else now
        retry_after = max(0.0, oldest + self.WINDOW_SECONDS - now)

        return RateLimitResult(
            allowed=False,
            remaining=0,
            retry_after=round(retry_after, 1),
            limit=limit,
        )
