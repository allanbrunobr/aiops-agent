"""Extração tolerante de JSON de respostas de LLM (F6-5).

Modelos frequentemente devolvem o JSON embrulhado em code-fence
(```json ... ```) ou com texto antes/depois. Antes, cinco call sites no
``AgentOrchestrator`` faziam ``json.loads(response.content[0].text)`` dentro de
``except Exception`` genérico: uma resposta válida com fence virava "parse
failed" silencioso e caía no plano/reflexão default — mascarando o problema.

Este helper tenta, nesta ordem: JSON puro → conteúdo do primeiro code-fence →
primeiro objeto/array balanceado no texto. Falha explícita (``LLMJsonError``)
com o texto original para o chamador decidir o fallback — e logar o motivo.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.S)


class LLMJsonError(ValueError):
    """A resposta do LLM não contém JSON parseável."""

    def __init__(self, text: str) -> None:
        self.text = text
        preview = text.strip().replace("\n", " ")[:120]
        super().__init__(f"LLM response is not JSON: {preview!r}")


class LLMBudgetExceeded(Exception):
    """Orçamento de chamadas LLM do run esgotado (NFR6).

    Definida em ``core`` para que ``agents`` (que não pode importar ``adapters``
    pela regra de camadas A6/ARQ-15) possa capturá-la nos call sites do
    orchestrator. ``adapters.llm.counting_client`` a reexporta por compatibilidade.
    """


def _balanced_slice(text: str) -> str | None:
    """Primeiro objeto ``{...}`` ou array ``[...]`` com delimitadores balanceados."""
    start = min((i for i in (text.find("{"), text.find("[")) if i != -1), default=-1)
    if start == -1:
        return None
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def extract_json(text: str) -> Any:
    """Devolve o JSON contido em ``text`` ou lança :class:`LLMJsonError`."""
    if text is None:
        raise LLMJsonError("")
    candidates: list[str] = [text.strip()]
    m = _FENCE.search(text)
    if m:
        candidates.append(m.group(1).strip())
    sliced = _balanced_slice(text)
    if sliced:
        candidates.append(sliced)
    for cand in candidates:
        if not cand:
            continue
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    raise LLMJsonError(text)


def extract_json_object(text: str) -> dict[str, Any]:
    """Como :func:`extract_json`, mas exige um objeto (dict)."""
    data = extract_json(text)
    if not isinstance(data, dict):
        raise LLMJsonError(text)
    return data


__all__ = ["LLMJsonError", "extract_json", "extract_json_object"]
