"""Porta ``Reporter`` — saida estruturada de eventos dos agentes (Fase 2c).

Esta porta isola os agentes (`agents/detector.py`, `agents/diagnoser.py`,
`agents/mitigator.py`) das bibliotecas de UI. Antes da Fase 2c esses agentes
importavam ``rich.console.Console`` diretamente e emitiam strings formatadas
para stdout, acoplando logica de negocio a uma escolha de apresentacao.

Apos a refatoracao, os agentes emitem **eventos nomeados com payload puro**
via ``Reporter.emit(event, **payload)``. A escolha de renderizacao (Rich,
log estruturado, mock para testes, no-op) fica nos adapters em
``adapters/reporter/``.

Vocabulario de eventos (constantes do modulo):

- ``INCIDENT_DETECTED``      — DetectionAgent terminou de classificar.
- ``DIAGNOSIS_STARTED``      — DiagnosisAgent comecou ReAct loop.
- ``ITERATION_STARTED``      — Inicio de cada iteracao do loop.
- ``LLM_CALL_FAILED``        — Chamada ao LLM levantou; agente caira em
  fallback heuristico.
- ``THOUGHT_OBSERVED``       — Agente emitiu raciocinio textual.
- ``TOOL_CALLED``            — Agente decidiu invocar uma tool.
- ``TOOL_OBSERVED``          — Resultado da tool foi recebido.
- ``DIAGNOSIS_COMPLETED``    — Diagnostico final disponivel; payload inclui
  ``mode`` (``"llm"`` ou ``"heuristic"``).
- ``MITIGATION_PROPOSED``    — MitigationAgent propos uma acao.
- ``HUMAN_APPROVAL_REQUESTED`` — Aguardando OK humano para executar.
- ``TNR_VERIFIED``           — Resultado da verificacao TNR (pre/post/result).

Adapters disponiveis em ``adapters/reporter/``:

- ``NullReporter`` (neste arquivo) — descarta eventos. Default seguro.
- ``RichConsoleReporter`` — preserva o output de CLI atual.
- ``StructuredLogReporter`` — emite JSON via ``logging`` para producao.
- ``FakeReporter`` (em ``tests/fakes``) — captura eventos para asserts.
"""

from __future__ import annotations

from typing import Any, Protocol

# === Vocabulario de eventos (mantenha esta lista pequena) ===
INCIDENT_DETECTED = "incident_detected"
DIAGNOSIS_STARTED = "diagnosis_started"
ITERATION_STARTED = "iteration_started"
LLM_CALL_FAILED = "llm_call_failed"
THOUGHT_OBSERVED = "thought_observed"
TOOL_CALLED = "tool_called"
TOOL_OBSERVED = "tool_observed"
DIAGNOSIS_COMPLETED = "diagnosis_completed"
MITIGATION_PROPOSED = "mitigation_proposed"
HUMAN_APPROVAL_REQUESTED = "human_approval_requested"
TNR_VERIFIED = "tnr_verified"


class Reporter(Protocol):
    """Contrato de emissao de eventos estruturados para a UI/log."""

    def emit(self, event: str, **payload: Any) -> None:
        """Emite um evento nomeado com payload arbitrario.

        ``event`` deve ser uma das constantes definidas neste modulo.
        Adapters podem ignorar eventos desconhecidos (forward-compat).
        """
        ...


class NullReporter:
    """Implementacao no-op — descarta todos os eventos.

    Usada como default quando nenhum reporter e injetado, mantendo agentes
    silenciosos em testes e integracoes que nao consumem o stream visual.
    """

    def emit(self, event: str, **payload: Any) -> None:
        return None
