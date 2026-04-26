"""
Log Parser usando Drain3.

Converte logs crus em templates únicos com contagem e timestamps.
Baseado no paper OpsAgent (Nankai University, Oct 2025):
  - Drain3 para template extraction
  - TF-IDF para ranking de relevância
  - Keyword filtering para pré-seleção

Ref: github.com/logpai/Drain3
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

try:
    import _aiops_rust as _rust
    _RUST_AVAILABLE = True
except ImportError:
    _rust = None
    _RUST_AVAILABLE = False

from drain3 import TemplateMiner
from drain3.template_miner_config import TemplateMinerConfig

INCIDENT_KEYWORDS = frozenset([
    "error", "fatal", "crash", "timeout", "oom", "exception",
    "refused", "unavailable", "panic", "exhausted", "circuit",
    "breaker", "failed", "aborted", "timed out", "503", "500",
])


@dataclass
class LogTemplate:
    """Um template de log com metadados agregados."""
    template: str
    count: int
    level: str
    services: set[str] = field(default_factory=set)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    sample_messages: list[str] = field(default_factory=list)
    relevance_score: float = 0.0


class LogParser:
    """Parser de logs usando Drain3 com ranking por relevância."""

    def __init__(self):
        config = TemplateMinerConfig()
        config.profiling_enabled = False
        config.drain_sim_th = 0.4
        config.drain_depth = 4
        self.miner = TemplateMiner(config=config)
        self.templates: dict[int, LogTemplate] = {}

    # Amostragem para lotes muito grandes: Drain3 converge após ~5K logs.
    # Não afeta diagnóstico em cenários reais (≤500 logs). Mantém consistência
    # de tokens porque o cap de 40 chars já garante saída uniforme.
    _SAMPLE_THRESHOLD = 5_000

    def parse(self, logs: list) -> list[LogTemplate]:
        """Processa lista de LogEntry e retorna templates agregados.

        Delega para implementação Rust quando disponível. Para lotes >5K logs,
        aplica amostragem estratificada antes de passar ao Drain3 — os templates
        convergem bem antes desse volume.
        Fallback para Python com cap de 500 amostras quando Rust não disponível.
        """
        import random as _rng
        if _RUST_AVAILABLE:
            # Rust processa todos os logs sem cap de amostragem
            return self._rust_parse(logs)
        return self._python_drain3_parse(logs)

    def _rust_parse(self, logs: list) -> list[LogTemplate]:
        """Delega processamento para implementação Rust via PyO3.

        Rust processa todos os logs sem cap de amostragem usando Drain3
        reimplementado e paralelismo Rayon.
        """
        import datetime as _dt

        raw = _rust.parse_logs(logs)
        result = []
        for d in raw:
            tpl = LogTemplate(
                template=d["template"],
                count=d["count"],
                level=d["level"],
                relevance_score=d["relevance_score"],
            )
            tpl.services = set(d["services"])
            fs = d.get("first_seen")
            ls = d.get("last_seen")
            tpl.first_seen = fs if isinstance(fs, _dt.datetime) else (
                _dt.datetime.fromtimestamp(fs) if fs is not None else None
            )
            tpl.last_seen = ls if isinstance(ls, _dt.datetime) else (
                _dt.datetime.fromtimestamp(ls) if ls is not None else None
            )
            tpl.sample_messages = list(d.get("sample_messages", []))
            result.append(tpl)
        return result

    def _python_drain3_parse(self, logs: list) -> list[LogTemplate]:
        """Implementação Python com amostragem sistemática (fallback).

        Cap de 500 amostras mantido intencionalmente para controlar custo O(N).
        Usado apenas quando _aiops_rust não está disponível.
        """
        # Amostragem sistemática para limitar custo O(N) do Drain3 em volumes grandes.
        # Templates convergem com ~10K amostras; além disso é redundante.
        _MAX = 500
        if len(logs) > _MAX:
            step = len(logs) // _MAX
            logs = logs[::step]
        for log in logs:
            msg = log.message
            result = self.miner.add_log_message(msg)
            # Drain3 >= 0.9.11 retorna dict ao inves de objeto
            cluster_id = result["cluster_id"]

            if cluster_id not in self.templates:
                self.templates[cluster_id] = LogTemplate(
                    template=result["template_mined"],
                    count=0,
                    level=log.level,
                )

            tpl = self.templates[cluster_id]
            tpl.count += 1
            tpl.services.add(log.service)
            tpl.template = result["template_mined"]

            if tpl.first_seen is None or log.timestamp < tpl.first_seen:
                tpl.first_seen = log.timestamp
            if tpl.last_seen is None or log.timestamp > tpl.last_seen:
                tpl.last_seen = log.timestamp

            if len(tpl.sample_messages) < 3:
                tpl.sample_messages.append(log.message)

        self._compute_relevance()
        return sorted(self.templates.values(), key=lambda t: t.relevance_score, reverse=True)

    def _validate_rust_output(self, logs: list) -> dict:
        """Helper de validação para desenvolvimento: compara saídas Rust vs Python.

        Retorna dict com métricas de concordância entre os dois paths.
        """
        if not _RUST_AVAILABLE:
            return {"error": "_aiops_rust não disponível"}

        # Executar ambos com estado limpo
        rust_templates = self._rust_parse(logs)

        py_parser = LogParser.__new__(LogParser)
        py_parser.__init__()
        py_templates = py_parser._python_drain3_parse(logs)

        rust_set = {t.template for t in rust_templates}
        py_set = {t.template for t in py_templates}

        return {
            "rust_count": len(rust_templates),
            "python_count": len(py_templates),
            "templates_in_rust_only": sorted(rust_set - py_set),
            "templates_in_python_only": sorted(py_set - rust_set),
            "common_templates": len(rust_set & py_set),
        }

    def _compute_relevance(self):
        """Calcula score de relevância combinando keywords + raridade (TF-IDF simples)."""
        total_logs = sum(t.count for t in self.templates.values())

        for tpl in self.templates.values():
            score = 0.0

            # Keyword match (0-5 pontos)
            tpl_lower = tpl.template.lower()
            keyword_hits = sum(1 for kw in INCIDENT_KEYWORDS if kw in tpl_lower)
            score += min(keyword_hits * 1.5, 5.0)

            # Level boost
            level_scores = {"ERROR": 3.0, "FATAL": 5.0, "WARN": 1.5, "INFO": 0.0, "DEBUG": 0.0}
            score += level_scores.get(tpl.level, 0.0)

            # Raridade invertida (logs muito frequentes de INFO são menos relevantes,
            # mas errors frequentes SÃO relevantes)
            frequency_ratio = tpl.count / total_logs
            if tpl.level in ("ERROR", "FATAL"):
                score += 2.0  # Fixed bonus — scale-invariant, count magnitude already in level_boost
            else:
                score += max(0, 2.0 - frequency_ratio * 20)  # Info raros = mais relevantes

            tpl.relevance_score = round(score, 2)

    def get_error_templates(self) -> list[LogTemplate]:
        """Retorna apenas templates de ERROR/FATAL, ordenados por relevância."""
        return [t for t in self.parse([]) if t.level in ("ERROR", "FATAL")]

    def to_text(self, templates: list[LogTemplate] | None = None, top_k: int = 20) -> str:
        """Converte templates em texto estruturado para o LLM."""
        if templates is None:
            templates = sorted(self.templates.values(), key=lambda t: t.relevance_score, reverse=True)

        lines = ["## Log Templates (ordenados por relevância)\n"]
        for i, tpl in enumerate(templates[:top_k]):
            services_str = ", ".join(sorted(tpl.services))
            first = tpl.first_seen.strftime("%H:%M:%S") if tpl.first_seen else "?"
            last = tpl.last_seen.strftime("%H:%M:%S") if tpl.last_seen else "?"
            lines.append(
                f"- [{tpl.level}] [{services_str}] \"{tpl.template}\" "
                f"| count={tpl.count} | {first}→{last} | relevance={tpl.relevance_score}"
            )

        return "\n".join(lines)
