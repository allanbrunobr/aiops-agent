"""Porta ``LLMClient`` — seam de teste e containment boundary (ADR 0002).

Esta porta NAO e abstracao para provider-swap. Sua finalidade e:

1. Substituir instanciacoes diretas de ``anthropic.Anthropic()`` por uma
   factory unica, removendo o acoplamento de import direto fora de
   ``adapters/llm/``.
2. Permitir testes sem monkey-patch do SDK Anthropic — fixtures podem
   implementar este Protocol em vez de mockar ``client.messages.create``
   no nivel do modulo.
3. Centralizar politicas transversais futuras (timeout, retry, metricas,
   rate limit) em um unico adapter.

A assinatura e deliberadamente passthrough (``**kwargs: Any -> Any``):
o payload aceita o dialeto Anthropic (``max_tokens``, ``system``, ``tools``
com ``input_schema``) e a resposta retorna o objeto Anthropic cru, cujo
shape (``response.content[*].type``, ``block.text``, ``tc.id/name/input``)
e consumido diretamente pelos call sites legados.

Tipos own-domain (``Message``, ``ToolDef``, ``ToolCall``, ``ChatResponse``)
ficam fora desta porta — serao introduzidos por ADR sucessor quando os
criterios de saida do ADR 0002 forem atingidos.
"""

from __future__ import annotations

from typing import Any, Protocol


class LLMClient(Protocol):
    """Contrato passthrough para envio de mensagens ao provider LLM.

    Ver docstring do modulo para escopo: seam, nao provider-swap.
    """

    def create_message(self, **kwargs: Any) -> Any:
        """Envia uma mensagem ao provider e devolve a resposta crua.

        ``kwargs`` e ``return`` mantem o dialeto Anthropic ate gatilho de
        saida documentado no ADR 0002.
        """
        ...
