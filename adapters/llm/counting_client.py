"""LLMClient que conta chamadas e aplica o orçamento por run (NFR6, F6-3).

``BlueprintEngine.increment_llm_calls`` existia mas nunca era chamado: o engine
não vê as chamadas, que acontecem dentro do ``AgentOrchestrator`` /
``DiagnosisAgent`` via ``LLMClient.create_message``. O limite só é real se for
aplicado nesse ponto — este wrapper faz isso. ``app.builders.build_agent_
orchestrator`` envolve o cliente com ``max_calls=config.blueprint.max_llm_calls``.
"""

from __future__ import annotations

from typing import Any

from application.ports.llm_client import LLMClient


class LLMBudgetExceeded(Exception):
    """Orçamento de chamadas LLM do run esgotado (NFR6)."""


class CountingLLMClient:
    """Envolve um ``LLMClient``; conta e limita ``create_message``.

    A chamada que ultrapassaria o limite NÃO é enviada ao provider — a exceção
    sobe antes, para não gastar tokens além do orçamento.
    """

    def __init__(self, inner: LLMClient, max_calls: int) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be >= 1")
        self._inner = inner
        self.max_calls = max_calls
        self.calls = 0

    def create_message(self, **kwargs: Any) -> Any:
        if self.calls + 1 > self.max_calls:
            raise LLMBudgetExceeded(
                f"LLM call limit exceeded: {self.calls + 1} > {self.max_calls}"
            )
        self.calls += 1
        return self._inner.create_message(**kwargs)

    @property
    def remaining(self) -> int:
        return max(0, self.max_calls - self.calls)


__all__ = ["CountingLLMClient", "LLMBudgetExceeded"]
