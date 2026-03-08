"""Utilitários para snapshot testing do pipeline AIOps Agent.

Gerencia criação, comparação e atualização de snapshots JSON
para validação de determinismo do pipeline com seed-42.

Zero dependências externas — usa apenas stdlib Python.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from difflib import unified_diff
from enum import Enum
from pathlib import Path

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
TIMESTAMP_PLACEHOLDER = "NORMALIZED_TIMESTAMP"

# Padrões de timestamp em texto livre (postmortem, report.to_text())
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_TIME_PATTERN = re.compile(r"\d{2}:\d{2}(:\d{2})?")

# Padrão para strings que são puramente timestamps (HH:MM:SS ou HH:MM)
_EXACT_TIME_PATTERN = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")

# Chaves cujas listas derivam de sets — devem ser ordenadas
_SET_LIKE_KEYS = frozenset({"affected_services"})

# Padrão para listas de serviços em strings de razão
# Ex: "Investigando incidente em auth-svc, payment-svc, order-svc, gateway"
_SERVICE_LIST_IN_TEXT = re.compile(r"(em\s+)([\w-]+(?:,\s*[\w-]+)+)")


def normalize_for_snapshot(
    data: object,
    *,
    _trace_counter: dict | None = None,
    _parent_key: str | None = None,
) -> object:
    """Normaliza dados do pipeline para comparação determinística.

    Converte recursivamente objetos do pipeline para representação
    JSON-serializable e determinística:
    - @dataclass → dict (via dataclasses.asdict)
    - Enum → .value
    - datetime → NORMALIZED_TIMESTAMP
    - set/frozenset → sorted list
    - float → round(6) para evitar flutuação
    - dict → chaves ordenadas
    - listas de affected_services → sorted (derivam de sets)
    - strings timestamp (HH:MM:SS) → NORMALIZED_TIMESTAMP
    - trace_id (str hex ≥16 chars) → placeholder sequencial
    - listas de serviços em strings de razão → sorted
    """
    if _trace_counter is None:
        _trace_counter = {"n": 0, "seen": {}}

    # Dataclass → dict e re-normaliza
    if is_dataclass(data) and not isinstance(data, type):
        return normalize_for_snapshot(
            asdict(data), _trace_counter=_trace_counter,
            _parent_key=_parent_key,
        )

    # Enum → valor primitivo
    if isinstance(data, Enum):
        return data.value

    # Dict → chaves ordenadas, valores normalizados com contexto de chave
    if isinstance(data, dict):
        return {
            k: normalize_for_snapshot(
                v, _trace_counter=_trace_counter, _parent_key=k,
            )
            for k, v in sorted(data.items())
        }

    # Lista/tupla → normaliza cada item
    if isinstance(data, (list, tuple)):
        items = [
            normalize_for_snapshot(item, _trace_counter=_trace_counter)
            for item in data
        ]
        # Listas derivadas de sets (ex: affected_services) — ordenar
        if _parent_key in _SET_LIKE_KEYS:
            return sorted(items, key=str)
        return items

    # Set/frozenset → sorted list para ordenação determinística
    if isinstance(data, (set, frozenset)):
        return sorted(
            normalize_for_snapshot(item, _trace_counter=_trace_counter)
            for item in data
        )

    # datetime → placeholder fixo
    if isinstance(data, datetime):
        return TIMESTAMP_PLACEHOLDER

    # Float → round para evitar diferenças de representação
    if isinstance(data, float):
        return round(data, 6)

    # String → normaliza timestamps, trace IDs e listas de serviços
    if isinstance(data, str):
        return _normalize_string(data, _trace_counter)

    return data


def _normalize_string(value: str, _trace_counter: dict) -> str:
    """Normaliza strings com timestamps, trace IDs e listas de serviços.

    Aplicado a todos os valores string durante normalização recursiva.
    """
    # Trace IDs (hex strings longas) → placeholder sequencial
    if _is_trace_id(value):
        seen = _trace_counter["seen"]
        if value not in seen:
            _trace_counter["n"] += 1
            seen[value] = f"trace_{_trace_counter['n']:03d}"
        return seen[value]

    # Strings que são puramente timestamps (ex: "09:01:52", "14:23")
    if _EXACT_TIME_PATTERN.match(value):
        return TIMESTAMP_PLACEHOLDER

    # Listas de serviços em strings de razão → ordenar para determinismo
    if _SERVICE_LIST_IN_TEXT.search(value):
        value = _sort_service_list_in_string(value)

    return value


def _is_trace_id(value: str) -> bool:
    """Verifica se a string parece ser um trace ID (hex ≥16 chars).

    Trace IDs são identificadores hexadecimais longos gerados
    aleatoriamente que variam entre execuções.
    """
    if len(value) < 16:
        return False
    return all(c in "0123456789abcdef-" for c in value.lower())


def _sort_service_list_in_string(text: str) -> str:
    """Ordena listas de serviços embutidas em strings de razão.

    Transforma 'em auth-svc, payment-svc, gateway'
    em 'em auth-svc, gateway, payment-svc' para determinismo.
    """
    def _sort_match(m: re.Match) -> str:
        prefix = m.group(1)
        services = [s.strip() for s in m.group(2).split(",")]
        return prefix + ", ".join(sorted(services))

    return _SERVICE_LIST_IN_TEXT.sub(_sort_match, text)


def normalize_text_timestamps(text: str) -> str:
    """Normaliza timestamps em texto livre (postmortem, report.to_text()).

    Substitui padrões de data (YYYY-MM-DD) e hora (HH:MM:SS) por
    placeholders para comparação determinística.
    Também ordena listas de serviços embutidas no texto.
    """
    result = _DATE_PATTERN.sub(TIMESTAMP_PLACEHOLDER, text)
    result = _TIME_PATTERN.sub(TIMESTAMP_PLACEHOLDER, result)
    # Ordena listas de serviços em texto livre (ex: razões no postmortem)
    result = _sort_service_list_in_string(result)
    return result


class SnapshotManager:
    """Gerencia snapshots JSON para testes de determinismo.

    Armazena snapshots como arquivos JSON em tests/snapshots/.
    Na primeira execução, cria o snapshot. Nas subsequentes, compara.
    Com --snapshot-update, sobrescreve snapshots existentes.
    """

    def __init__(self, snapshot_dir: Path | None = None, *, update: bool = False):
        self.snapshot_dir = snapshot_dir or SNAPSHOT_DIR
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.update = update

    def assert_match(self, data: object, snapshot_name: str) -> None:
        """Compara dados normalizados contra snapshot armazenado.

        Se o snapshot não existe, cria-o (primeira execução).
        Se --snapshot-update foi passado, sobrescreve o existente.
        Caso contrário, compara e falha com diff legível se diferir.
        """
        snapshot_path = self.snapshot_dir / f"{snapshot_name}.json"
        normalized = normalize_for_snapshot(data)
        current = json.dumps(
            normalized, indent=2, sort_keys=True,
            ensure_ascii=False, default=str,
        )

        if self.update or not snapshot_path.exists():
            snapshot_path.write_text(current, encoding="utf-8")
            return

        stored = snapshot_path.read_text(encoding="utf-8")
        if current != stored:
            diff_lines = list(unified_diff(
                stored.splitlines(), current.splitlines(),
                fromfile=f"stored/{snapshot_name}.json",
                tofile=f"current/{snapshot_name}.json",
                lineterm="",
            ))
            diff = "\n".join(diff_lines)
            raise AssertionError(
                f"Snapshot mismatch para '{snapshot_name}'.\n"
                f"Execute com --snapshot-update para regenerar.\n\n{diff}"
            )

    def assert_text_match(self, text: str, snapshot_name: str) -> None:
        """Compara texto normalizado contra snapshot armazenado.

        Usado para outputs de texto como report.to_text() e postmortem.
        Timestamps no texto são normalizados antes da comparação.
        """
        snapshot_path = self.snapshot_dir / f"{snapshot_name}.txt"
        normalized = normalize_text_timestamps(text)

        if self.update or not snapshot_path.exists():
            snapshot_path.write_text(normalized, encoding="utf-8")
            return

        stored = snapshot_path.read_text(encoding="utf-8")
        if normalized != stored:
            diff_lines = list(unified_diff(
                stored.splitlines(), normalized.splitlines(),
                fromfile=f"stored/{snapshot_name}.txt",
                tofile=f"current/{snapshot_name}.txt",
                lineterm="",
            ))
            diff = "\n".join(diff_lines)
            raise AssertionError(
                f"Snapshot mismatch para '{snapshot_name}'.\n"
                f"Execute com --snapshot-update para regenerar.\n\n{diff}"
            )
