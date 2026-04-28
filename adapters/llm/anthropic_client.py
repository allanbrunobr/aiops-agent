"""Adapter Anthropic — seam de teste e containment boundary (ADR 0002).

Este adapter NAO e provider-swap. Seu papel e:

- Ser o unico arquivo do projeto que importa ``anthropic`` diretamente
  (containment boundary).
- Implementar a porta ``application.ports.llm_client.LLMClient`` via
  ``create_message(**kwargs)`` que delega para ``client.messages.create``.
- Expor uma factory ``create_anthropic_llm_client(api_key)`` consumida por
  ``main.py``, ``blueprint/adapters/pipeline_nodes.py`` e ``agents/diagnoser.py``.

Os call sites legados em ``agents/orchestrator.py`` e ``agents/skill_evolver.py``
ainda chamam ``client.messages.create(...)`` diretamente — ate migracao,
o passthrough ``messages`` (property) preserva esse uso. Documentado como
debito tecnico no ADR 0002.
"""

from __future__ import annotations

from typing import Any

from anthropic import Anthropic


class AnthropicLLMClient:
    """Wrapper fino sobre ``anthropic.Anthropic``.

    Implementa o Protocol ``LLMClient`` (passthrough) e expoe ``messages``
    como property para callers legados que ainda usam ``client.messages.create``.
    Quando ``orchestrator``/``skill_evolver`` migrarem para ``create_message``,
    o passthrough sera removido.
    """

    def __init__(self, client: Anthropic) -> None:
        self._client = client

    def create_message(self, **kwargs: Any) -> Any:
        """Delega para ``self._client.messages.create(**kwargs)``.

        Mantem o shape Anthropic (``Message`` com ``content``, ``stop_reason``,
        etc.) — ver ADR 0002 para escopo desta passthrough.
        """
        return self._client.messages.create(**kwargs)

    @property
    def messages(self) -> Any:
        """Passthrough para ``client.messages`` — preservado para callers legados.

        Existe apenas porque ``agents/orchestrator.py`` e ``agents/skill_evolver.py``
        ainda usam ``client.messages.create(...)`` direto. Sera removido quando
        esses call sites migrarem para ``create_message``. Ver ADR 0002.
        """
        return self._client.messages


def create_anthropic_llm_client(api_key: str | None = None) -> AnthropicLLMClient:
    """Factory para o adapter Anthropic.

    Quando ``api_key`` e ``None``, ``Anthropic`` le ``ANTHROPIC_API_KEY``
    do ambiente — comportamento preservado dos call sites originais.
    """
    return AnthropicLLMClient(Anthropic(api_key=api_key))
