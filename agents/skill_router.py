"""
Skill Router — seleciona a melhor DiagnosticSkill para um incidente.

Fluxo:
  1. Extrai tags do incidente (via _extract_fault_tags em main.py)
  2. Busca skill com maior utility_score no IncidentMemoryDB
  3. Retorna DEFAULT_SKILL se nenhuma skill qualificada for encontrada
"""

from __future__ import annotations

import uuid

from rag.memory import DiagnosticSkill, IncidentMemoryDB

# Prompt genérico — mesmo do SYSTEM_PROMPT do DiagnosisAgent.
# Skills especializadas substituem este prompt conforme evoluem.
_DEFAULT_SYSTEM_PROMPT = """You are an expert SRE agent performing Root Cause Analysis on a production incident.

You have access to the following tools:
1. query_logs(service, keyword) - Search logs for a specific service and keyword
2. query_metrics(service, metric_name) - Get metric details for a service
3. query_traces(service) - Get slow traces for a service
4. search_similar(query) - Semantic search across all observability data
5. correlate_signals(service) - Cross-correlate signals across logs, metrics, traces
6. reconstruct_timeline(services) - Build chronological event timeline
7. classify_fault_type(service) - Classify probable fault category

RULES:
- Think step by step. First understand WHAT is failing, then WHY.
- Use tools to gather evidence. Don't guess.
- Cross-correlate logs, metrics, and traces to build a complete picture.

When you have enough evidence, provide your final diagnosis in this JSON format:
```json
{"root_cause": "...", "evidence": [...], "affected_services": [...], "confidence": "high|medium|low", "timeline": "..."}
```
Wrap your final answer in <DIAGNOSIS> tags."""

_POOL_EXHAUSTION_PROMPT = """You are an expert SRE agent diagnosing a Connection Pool Exhaustion incident.

SPECIALIZATION: Connection Pool Exhaustion
Based on patterns from similar incidents, prioritize:
1. Check connection pool metrics FIRST (query_metrics with metric_name=connection_pool)
2. Look for config changes in deployment logs (query_logs with keyword=config OR deploy)
3. Correlate pool size changes with error rate spikes (correlate_signals)
4. Check max_connections configuration across the deployment timeline

COMMON ROOT CAUSES FOR THIS TYPE:
- Config change reduced max_connections during deploy
- Connection leak in application code (connections not released)
- Sudden traffic spike exceeding pool capacity

When you have enough evidence, provide your final diagnosis in JSON wrapped in <DIAGNOSIS> tags."""

_MEMORY_LEAK_PROMPT = """You are an expert SRE agent diagnosing a Memory Leak incident.

SPECIALIZATION: Memory Exhaustion / OOM
Based on patterns from similar incidents, prioritize:
1. Check memory metrics (query_metrics with metric_name=memory OR heap)
2. Look for OOM kill events in logs (query_logs with keyword=oom OR killed)
3. Reconstruct timeline of memory growth (reconstruct_timeline)
4. Correlate memory growth with deployment events

COMMON ROOT CAUSES FOR THIS TYPE:
- Memory leak in application code (objects not garbage collected)
- Large in-memory cache without eviction policy
- Memory pressure from increased load

When you have enough evidence, provide your final diagnosis in JSON wrapped in <DIAGNOSIS> tags."""


DEFAULT_SKILL = DiagnosticSkill(
    skill_id="default",
    name="unknown-fault-diagnosis",
    scenario_pattern=".*",
    system_prompt=_DEFAULT_SYSTEM_PROMPT,
    priority_tools=["classify_fault_type", "correlate_signals", "search_similar"],
    utility_score=0.3,  # score baixo — só usado como fallback
)


class SkillRouter:
    """Seleciona a melhor DiagnosticSkill para um incidente."""

    SCORE_THRESHOLD = 0.3  # skills abaixo deste score são ignoradas

    def __init__(self, memory_db: IncidentMemoryDB) -> None:
        self.db = memory_db

    def route(self, tags: list[str], services: list[str]) -> DiagnosticSkill:
        """Busca a melhor skill. Retorna DEFAULT_SKILL se nenhuma qualificar."""
        skill = self.db.find_best_skill(tags, services)
        if skill and skill.utility_score >= self.SCORE_THRESHOLD:
            return skill
        return DEFAULT_SKILL

    def seed_default_skills(self) -> None:
        """Popula skills iniciais para cenários conhecidos (idempotente).

        Usa skill_ids determinísticos para evitar duplicatas.
        Não sobrescreve skills que já foram evoluídas (version > 1).
        """
        defaults = [
            DiagnosticSkill(
                skill_id="seed-connection-pool",
                name="connection-pool-diagnosis",
                scenario_pattern="connection pool|pool exhausted|max_connections",
                system_prompt=_POOL_EXHAUSTION_PROMPT,
                priority_tools=["query_metrics", "correlate_signals", "query_logs"],
            ),
            DiagnosticSkill(
                skill_id="seed-memory-leak",
                name="memory-leak-diagnosis",
                scenario_pattern="memory|oom|heap|leak",
                system_prompt=_MEMORY_LEAK_PROMPT,
                priority_tools=["query_metrics", "reconstruct_timeline", "query_logs"],
            ),
            DiagnosticSkill(
                skill_id="default",
                name="unknown-fault-diagnosis",
                scenario_pattern=".*",
                system_prompt=_DEFAULT_SYSTEM_PROMPT,
                priority_tools=["classify_fault_type", "correlate_signals", "search_similar"],
                utility_score=0.3,
            ),
        ]
        for skill in defaults:
            existing = self.db.get_skill(skill.skill_id)
            if existing is None:
                self.db.save_skill(skill)
