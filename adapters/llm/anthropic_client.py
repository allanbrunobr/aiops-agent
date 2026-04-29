"""Adapter Anthropic — seam de teste e containment boundary (ADR 0002).

Este adapter NAO e provider-swap. Seu papel e:

- Ser o unico arquivo do projeto que importa ``anthropic`` diretamente
  (containment boundary).
- Implementar a porta ``application.ports.llm_client.LLMClient`` via
  ``create_message(**kwargs)`` que delega para ``client.messages.create``.
- Expor uma factory ``create_anthropic_llm_client(api_key)`` consumida pelos
  call sites do projeto.

Pos-Fase 2b, todos os call sites internos (``main.py``, blueprint adapters,
``agents/diagnoser.py``, ``agents/orchestrator.py``, ``agents/skill_evolver.py``)
usam ``create_message``. Nenhum acessa ``.messages`` diretamente — o
passthrough foi removido.
"""

from __future__ import annotations

from typing import Any

from anthropic import Anthropic


class AnthropicLLMClient:
    """Wrapper fino sobre ``anthropic.Anthropic`` que implementa ``LLMClient``.

    Expoe apenas ``create_message(**kwargs)`` como contrato publico. Acesso
    direto ao SDK Anthropic (``client.messages.create``, ``client.beta.*``,
    etc.) so e permitido neste arquivo.
    """

    def __init__(self, client: Anthropic) -> None:
        self._client = client

    def create_message(self, **kwargs: Any) -> Any:
        """Delega para ``self._client.messages.create(**kwargs)``.

        Mantem o shape Anthropic (``Message`` com ``content``, ``stop_reason``,
        etc.) — ver ADR 0002 para escopo desta passthrough.
        """
        return self._client.messages.create(**kwargs)


def create_anthropic_llm_client(api_key: str | None = None) -> AnthropicLLMClient:
    """Factory para o adapter Anthropic.

    Quando ``api_key`` e ``None``, ``Anthropic`` le ``ANTHROPIC_API_KEY``
    do ambiente — comportamento preservado dos call sites originais.
    """
    return AnthropicLLMClient(Anthropic(api_key=api_key))
