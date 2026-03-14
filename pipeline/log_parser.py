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

    def parse(self, logs: list) -> list[LogTemplate]:
        """Processa lista de LogEntry e retorna templates agregados."""
        # Amostragem sistemática para limitar custo O(N) do Drain3 em volumes grandes.
        # Templates convergem com ~10K amostras; além disso é redundante.
        _MAX = 10_000
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
                tpl.sample_messages.append(str(log))

        self._compute_relevance()
        return sorted(self.templates.values(), key=lambda t: t.relevance_score, reverse=True)

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
