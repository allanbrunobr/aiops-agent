"""
AIOps Agent - Demo de Incident Response com LLM.

Pipeline completo:
  1. Gera dados simulados (logs, metricas, traces)
  1b. Sanitiza telemetria contra adversarial reward-hacking (AIOpsShield)
  2. Pre-processa com Drain3, 3-sigma, p95
  2b. Comprime contexto para otimizar tokens do LLM (Acon + AOI)
  3. Indexa com FAISS para RAG
  4. Executa pipeline de agentes: Detect -> Diagnose (ReAct) -> Mitigate -> Verify (TNR)
  4b. Valida seguranca pre-execucao (Pre-Validator + TNR)
  4c. Valida versoes/CVEs na remediacao proposta (VersionValidator)
  5. Gera postmortem

Baseado nos papers:
  - STRATUS (NeurIPS 2025), OpsAgent (Nankai 2025), Microsoft RCA Agent (2024)
  - Acon (Microsoft 2025), AOI (Columbia 2025), TAMO (Shandong 2025)
  - "When AIOps Become AI Oops" (RSAC Labs + George Mason U., 2025)

Uso:
  python main.py                              # demo completo (cenario default: payment-pool-exhaustion)
  python main.py --scenario memory-leak       # seleciona cenario de incidente
  python main.py --data-source databricks     # usa dados reais do Databricks
  python main.py --data-source simulated      # usa dados simulados (default)
  python main.py --config /path/to/aiops.toml  # usa arquivo de configuracao de thresholds
  python main.py --output-format json         # saida JSON machine-readable
  python main.py --output-format both         # gera markdown E JSON
  python main.py --tracker jira               # cria issue Jira automaticamente apos diagnostico
  python main.py --tracker none               # desativa tracking explicitamente
  python main.py --no-llm                     # sem chamada ao Claude (heuristica apenas)
  python main.py --no-rag                     # pula indexacao RAG (mais rapido)
  python main.py --rebuild-rag                # forca reconstrucao do indice RAG (ignora cache)
  python main.py --compact                    # output compacto
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import json
import os
import re
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path

from config.loader import AppConfig, load_config
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown
from rich.rule import Rule
from rich.text import Text

# Modulos do projeto
from data.models import SimulatedIncident
from data.sample_incident import create_incident
from data.registry import get_scenario, list_scenarios
from pipeline.aggregator import Aggregator, AggregatedReport
from rag.indexer import ObservabilityIndexer
from rag.retriever import ObservabilityRetriever
from rag.retriever_history import ObservabilityRetrieverWithHistory
from rag.memory import IncidentMemoryDB, IncidentRecord
from agents.detector import DetectionAgent
from agents.diagnoser import DiagnosisAgent
from agents.mitigator import MitigationAgent
from safety.tnr import TNRSafetyGuard
from pipeline.context_compressor import ContextCompressor
from safety.pre_validator import PreExecutionValidator
from pipeline.telemetry_sanitizer import TelemetrySanitizer
from safety.version_validator import VersionValidator
from safety.orchestrator import SafetyOrchestrator

console = Console()

# Exit codes para pipeline outcomes (Story 3.2)
EXIT_SUCCESS = 0           # Pipeline resolved (TNR success)
EXIT_ERROR = 1             # Unrecoverable error (exception in any stage)
EXIT_REGRESSION = 2        # TNR detected regression (post-health worse)
EXIT_APPROVAL_NEEDED = 3   # Safety blocked action (human approval required)

EXIT_CODE_LABELS = {
    EXIT_SUCCESS: "resolved",
    EXIT_ERROR: "error",
    EXIT_REGRESSION: "regression",
    EXIT_APPROVAL_NEEDED: "approval-needed",
}

# Padrões de API key para sanitização de secrets (Story 3.3)
API_KEY_PATTERNS = [
    re.compile(r"sk-ant-[a-zA-Z0-9_-]{20,}"),      # Anthropic API keys
    re.compile(r"dapi[a-zA-Z0-9]{20,}"),             # Databricks API keys
    re.compile(r"sk-[a-zA-Z0-9]{32,}"),              # OpenAI-style keys
    re.compile(r"key-[a-zA-Z0-9]{32,}"),             # Generic API keys
]


def _sanitize_secrets(text: str) -> str:
    """Remove padrões de API key do texto, substituindo por [REDACTED]."""
    for pattern in API_KEY_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _contains_api_key_pattern(text: str) -> bool:
    """Verifica se o texto contém algum padrão de API key."""
    return any(pattern.search(text) for pattern in API_KEY_PATTERNS)


def print_banner():
    """Exibe banner do projeto."""
    banner = Text()
    banner.append("AIOps Agent", style="bold cyan")
    banner.append(" - Agentic Incident Response Demo\n", style="dim")
    banner.append("Based on: ", style="dim")
    banner.append("STRATUS", style="bold")
    banner.append(" + ", style="dim")
    banner.append("OpsAgent", style="bold")
    banner.append(" + ", style="dim")
    banner.append("RCACopilot", style="bold")
    banner.append(" + ", style="dim")
    banner.append("Acon", style="bold")
    banner.append(" + ", style="dim")
    banner.append("AOI", style="bold")
    banner.append(" + ", style="dim")
    banner.append("TAMO", style="bold")
    banner.append(" + ", style="dim")
    banner.append("AIOpsShield", style="bold")
    console.print(Panel(banner, border_style="cyan", padding=(1, 2)))


def step_generate_data(scenario_name: str | None = None) -> tuple[SimulatedIncident, str]:
    """Etapa 1: Gera dados simulados de incidente.

    Args:
        scenario_name: Nome do cenário a usar. Se None, usa payment-pool-exhaustion (default).

    Returns:
        Tupla (incidente simulado, nome efetivo do cenário).
    """
    console.print(Rule("[bold]Stage 1: Data Generation[/bold]", style="cyan"))
    console.print()

    if scenario_name:
        incident = get_scenario(scenario_name)(seed=42)
        effective_scenario = scenario_name
    else:
        incident = create_incident()
        effective_scenario = "payment-pool-exhaustion"

    num_services = len({log.service for log in incident.logs})

    table = Table(title="Simulated Incident Data", border_style="cyan", show_header=True)
    table.add_column("Source", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Details")
    table.add_row("Scenario", "", f"[bold]{effective_scenario}[/bold]")
    table.add_row("Logs", str(len(incident.logs)), f"{num_services} services, ERROR/WARN/INFO mix")
    table.add_row("Metrics", str(len(incident.metrics)), "Multi-service telemetry series")
    table.add_row("Traces", str(len(incident.traces)), "Distributed spans with bottleneck")
    console.print(table)

    console.print(f"\n  [dim]Root cause: {incident.actual_root_cause}[/dim]")
    deploy = incident.deploy_time.strftime("%H:%M")
    spike = incident.spike_start.strftime("%H:%M")
    detect = incident.detection_time.strftime("%H:%M")
    fix = incident.fix_time.strftime("%H:%M")
    console.print(f"  [dim]Timeline: deploy@{deploy} -> spike@{spike} -> detect@{detect} -> fix@{fix}[/dim]\n")

    return incident, effective_scenario


def step_sanitize(incident):
    """Etapa 1b: Sanitiza telemetria contra adversarial reward-hacking."""
    console.print(Rule("[bold]Stage 1b: Telemetry Sanitization (AIOpsShield)[/bold]", style="cyan"))
    console.print()

    t0 = time.time()
    sanitizer = TelemetrySanitizer(strict_mode=False)
    s_logs, s_metrics, s_traces, san_report = sanitizer.sanitize_all(
        incident.logs, incident.metrics, incident.traces,
    )
    elapsed = time.time() - t0

    # Display results
    table = Table(title="Telemetry Sanitization Results", border_style="red" if san_report.threat_detected else "green",
                  show_header=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total entries analyzed", str(san_report.total_entries))
    table.add_row("Clean entries", f"[green]{san_report.clean_entries}[/green]")
    table.add_row("Suspicious entries", f"[yellow]{san_report.suspicious_entries}[/yellow]" if san_report.suspicious_entries > 0 else "0")
    table.add_row("Tainted entries", f"[red]{san_report.tainted_entries}[/red]" if san_report.tainted_entries > 0 else "0")
    table.add_row("Adversarial entries", f"[bold red]{san_report.adversarial_entries}[/bold red]" if san_report.adversarial_entries > 0 else "0")
    table.add_row("Fields sanitized", str(san_report.fields_sanitized))
    console.print(table)

    if san_report.threat_detected:
        console.print(f"  [bold red]🛡️ ADVERSARIAL PAYLOADS BLOCKED:[/bold red]")
        for i, payload in enumerate(san_report.adversarial_payloads_blocked[:5], 1):
            console.print(f"    {i}. [dim]{payload[:100]}...[/dim]")
    else:
        console.print(f"  [green]✅ No adversarial payloads detected[/green]")

    console.print(f"  [dim]Sanitization time: {elapsed:.3f}s[/dim]")
    console.print(f"  [dim]Defense: AIOpsShield (taint analysis + pattern matching + abstraction)[/dim]\n")

    # Update incident data with sanitized versions
    incident.logs = s_logs
    incident.metrics = s_metrics
    incident.traces = s_traces

    return san_report


def step_pipeline(incident, config: AppConfig | None = None) -> AggregatedReport:
    """Etapa 2: Pre-processamento (Drain3 + 3-sigma + p95)."""
    console.print(Rule("[bold]Stage 2: Pre-Processing Pipeline[/bold]", style="cyan"))
    console.print()

    if config is None:
        config = AppConfig()

    t0 = time.time()
    aggregator = Aggregator(
        sigma_threshold=config.thresholds.sigma_threshold,
        baseline_ratio=config.thresholds.baseline_ratio,
        percentile_threshold=config.thresholds.percentile_threshold,
        max_hops=config.thresholds.max_hops,
    )
    report = aggregator.process(incident.logs, incident.metrics, incident.traces)
    elapsed = time.time() - t0

    table = Table(title="Pipeline Results", border_style="green", show_header=True)
    table.add_column("Step", style="bold")
    table.add_column("Input", justify="right")
    table.add_column("Output", justify="right")
    table.add_column("Reduction", justify="right")
    table.add_row(
        "Drain3 (logs)",
        str(report.total_logs),
        f"{len(report.log_templates)} templates",
        f"{report.total_logs / max(len(report.log_templates), 1):.0f}x",
    )
    table.add_row(
        "3-sigma (metrics)",
        str(report.total_metrics),
        f"{len(report.metric_anomalies)} anomalies",
        f"{report.total_metrics / max(len(report.metric_anomalies), 1):.0f}x",
    )
    table.add_row(
        "p95 (traces)",
        str(report.total_traces),
        f"{len(report.slow_traces)} slow paths",
        f"{report.total_traces / max(len(report.slow_traces), 1):.0f}x",
    )
    console.print(table)
    console.print(f"  [dim]Processing time: {elapsed:.2f}s[/dim]")
    console.print(f"  [dim]Severity: {report.severity}[/dim]")
    console.print(f"  [dim]Affected services: {', '.join(sorted(report.affected_services))}[/dim]\n")

    return report


def step_compression(report):
    """Etapa 2b: Context Compression (Acon + AOI)."""
    console.print(Rule("[bold]Stage 2b: Context Compression (Acon + AOI)[/bold]", style="cyan"))
    console.print()

    compressor = ContextCompressor(compression_level="balanced")

    # Comprimir o report
    t0 = time.time()
    compressed_text, stats = compressor.compress(report)
    elapsed = time.time() - t0

    # Display stats
    table = Table(title="Context Compression Results", border_style="magenta", show_header=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    # Original tokens uses report.to_text() length for comparison
    original_full_tokens = len(report.to_text()) // 4  # rough token estimate
    actual_ratio = 1 - (stats.compressed_tokens / max(original_full_tokens, 1))

    table.add_row("Full report tokens (est.)", f"{original_full_tokens:,}")
    table.add_row("Compressed tokens (est.)", f"{stats.compressed_tokens:,}")
    table.add_row("Compression ratio", f"{max(actual_ratio, 0):.0%}" if actual_ratio > 0 else "0% (input within budget)")
    table.add_row("Chunks preserved", str(stats.chunks_kept))
    table.add_row("Chunks removed", str(stats.chunks_removed))
    table.add_row("Critical info preserved", f"{min(stats.critical_preserved, 100):.0f}%")
    console.print(table)

    console.print(f"  [dim]Compression time: {elapsed:.3f}s[/dim]")
    console.print(f"  [dim]Level: balanced (priority-based classification + budget enforcement)[/dim]")

    # Show priority breakdown
    by_priority = {}
    for name, count in [("CRITICAL", 0), ("IMPORTANT", 0), ("CONTEXT", 0), ("NOISE", 0)]:
        by_priority[name] = count
    # Count from the classified items in the compressor
    if hasattr(compressor, '_last_classified'):
        for item in compressor._last_classified:
            by_priority[item.priority.name] = by_priority.get(item.priority.name, 0) + 1

    console.print(f"  [dim]Compressed output: {len(compressed_text)} chars → ready for LLM[/dim]\n")

    return compressor, compressed_text, stats


def _extract_fault_tags(report) -> list[str]:
    """Extrai tags de falha heurísticas a partir do report agregado.

    Analisa templates de log e anomalias de métricas para inferir tags
    como 'connection-pool', 'config-change', 'error-spike', etc.

    Args:
        report: AggregatedReport com dados pré-processados.

    Returns:
        Lista de tags de falha únicas (strings kebab-case).
    """
    tags: set[str] = set()

    for tpl in report.log_templates:
        template_lower = str(tpl.template).lower()
        if "connection" in template_lower and ("pool" in template_lower or "timeout" in template_lower):
            tags.add("connection-pool")
        if "config" in template_lower or "configuration" in template_lower:
            tags.add("config-change")
        if tpl.level in ("ERROR", "FATAL") and tpl.count > 100:
            tags.add("error-spike")
        if "memory" in template_lower or "oom" in template_lower or "heap" in template_lower:
            tags.add("memory-leak")
        if "timeout" in template_lower:
            tags.add("timeout")
        if "deploy" in template_lower or "rollout" in template_lower:
            tags.add("deployment")

    for anomaly in report.metric_anomalies:
        metric_lower = str(anomaly.metric_name).lower()
        if "connection" in metric_lower or "pool" in metric_lower:
            tags.add("connection-pool")
        if "memory" in metric_lower or "heap" in metric_lower:
            tags.add("memory-leak")
        if "error" in metric_lower or "failure" in metric_lower:
            tags.add("error-spike")
        if anomaly.deviation_sigma > 10:
            tags.add("high-sigma-anomaly")

    return sorted(tags)


def step_rag(report, cache_dir: str = ".aiops-cache/rag", rebuild: bool = False):
    """Etapa 3: Indexacao RAG com FAISS.

    Tenta carregar indice do cache em disco. Se cache valido e rebuild=False,
    pula re-embedding. Caso contrario, reconstroi e salva em disco.

    Args:
        report: AggregatedReport com dados pre-processados.
        cache_dir: Diretorio para cache do indice FAISS.
        rebuild: Se True, forca reconstrucao ignorando cache.
    """
    console.print(Rule("[bold]Stage 3: RAG Indexing (FAISS)[/bold]", style="cyan"))
    console.print()

    t0 = time.time()
    from tools.faiss_setup import setup_faiss_index
    indexer = setup_faiss_index() or ObservabilityIndexer()

    # Calcula hash do conteudo atual para deteccao de stale
    content_hash = ObservabilityIndexer._compute_content_hash(
        report.log_templates,
        report.metric_anomalies,
        report.slow_traces,
    )

    loaded_from_cache = False
    if not rebuild:
        loaded_from_cache = indexer.load_index(cache_dir, content_hash=content_hash)

    if loaded_from_cache:
        # Modelo precisa ser carregado mesmo com cache — usado para query embedding
        indexer._load_model()
        num_chunks = len(indexer.chunks)
        elapsed = time.time() - t0
        console.print(f"  Loaded [bold]{num_chunks}[/bold] chunks from cache in {elapsed:.2f}s")
        console.print(f"  Cache: [dim]{cache_dir}[/dim]")
    else:
        if rebuild:
            console.print(f"  [dim]--rebuild-rag: forcing index reconstruction[/dim]")
        num_chunks = indexer.build_index(
            report.log_templates,
            report.metric_anomalies,
            report.slow_traces,
        )
        # Salva indice em disco para reutilizacao futura
        try:
            indexer.save_index(cache_dir, content_hash=content_hash)
            console.print(f"  [dim]Index saved to {cache_dir}[/dim]")
        except Exception as exc:
            console.print(f"  [yellow]Warning: could not save index cache: {exc}[/yellow]")
        elapsed = time.time() - t0
        console.print(f"  Indexed [bold]{num_chunks}[/bold] chunks in {elapsed:.2f}s")

    console.print(f"  Model: [dim]all-MiniLM-L6-v2 (384 dimensions)[/dim]")

    retriever = ObservabilityRetrieverWithHistory(indexer)

    # Demo query
    console.print(f"\n  [dim]Sample query: 'connection pool timeout'[/dim]")
    results = retriever.search("connection pool timeout", top_k=3)
    for i, r in enumerate(results, 1):
        console.print(f"    {i}. [{r.chunk.source.upper()}] (score={r.score:.3f}) {r.chunk.text[:100]}...")

    # Exibe precedentes históricos antes do diagnóstico
    try:
        services = list(report.affected_services)
        tags = _extract_fault_tags(report)
        precedents = retriever.get_similar_past_incidents(services, tags, limit=3)
        if precedents:
            console.print(f"\n  [bold cyan]Similar past incidents found:[/bold cyan]")
            for p in precedents:
                console.print(f"    • {p['scenario']}: {p['root_cause']}")
    except Exception:
        pass

    console.print()
    return retriever


def _extract_fault_tags(report) -> list[str]:
    """Extrai fault tags do AggregatedReport para roteamento de skills."""
    tags: list[str] = []
    for tpl in getattr(report, "log_templates", []):
        tpl_lower = tpl.template.lower()
        if "connection pool" in tpl_lower or "pool exhausted" in tpl_lower:
            tags.append("connection_pool")
        if "memory" in tpl_lower or "oom" in tpl_lower or "heap" in tpl_lower:
            tags.append("memory_leak")
        if "timeout" in tpl_lower:
            tags.append("timeout")
        if "serial" in tpl_lower or "schema" in tpl_lower:
            tags.append("serialization_error")
    return list(set(tags)) or ["unknown"]


def step_agents(report, retriever, use_llm: bool = True,
                llm_model: str = "claude-sonnet-4-20250514",
                config: AppConfig | None = None) -> dict:
    """Etapa 4: Pipeline de agentes (Detect -> Diagnose -> Mitigate -> Verify).

    Args:
        report: AggregatedReport do pipeline.
        retriever: ObservabilityRetriever para RAG (ou None).
        use_llm: Se True, usa LLM para diagnostico. Se False, heuristica apenas.
        llm_model: Nome do modelo LLM a usar (ignorado se use_llm=False).
        config: AppConfig com valores de thresholds para agentes.
    """
    console.print(Rule("[bold]Stage 4: Agent Pipeline[/bold]", style="cyan"))
    console.print()

    if config is None:
        config = AppConfig()

    # ── Skill Evolution: READ + ROUTE ─────────────────────────────────────
    # 1. Consulta memória para injetar precedentes relevantes no diagnóstico.
    # 2. Seleciona a melhor DiagnosticSkill para o tipo de incidente detectado.
    from agents.skill_router import SkillRouter
    _memory_db = IncidentMemoryDB()
    _fault_tags = _extract_fault_tags(report)
    _similar_incidents = _memory_db.find_similar_incidents(
        affected_services=list(report.affected_services),
        tags=_fault_tags,
        limit=3,
    )
    _skill_router = SkillRouter(_memory_db)
    # Seed default skills na primeira execução (idempotente se já existem)
    _skill_router.seed_default_skills()
    _skill = _skill_router.route(_fault_tags, list(report.affected_services))
    console.print(
        f"  [dim]Skill selected: {_skill.name} "
        f"(utility={_skill.utility_score:.2f})[/dim]"
    )
    # ─────────────────────────────────────────────────────────────────────

    # Inicializa agentes (Estágio 1 / fallback --no-llm)
    detector = DetectionAgent(
        error_threshold=config.thresholds.error_threshold,
        sigma_threshold=config.thresholds.detection_sigma_threshold,
    )

    if use_llm:
        # Estágio 2: agente autônomo com plan/execute/reflect
        from anthropic import Anthropic
        from agents.orchestrator import AgentOrchestrator

        _client = Anthropic()
        _orchestrator = AgentOrchestrator(
            client=_client,
            model=llm_model,
            memory_db=_memory_db,
            skill_router=_skill_router,
            config=config,
        )
    # Estágio 1 (fallback --no-llm): DiagnosisAgent heurístico
    diagnoser = DiagnosisAgent(
        max_iterations=1,
        model=llm_model,
        similar_incidents=_similar_incidents,
        skill=_skill,
    )
    mitigator = MitigationAgent()
    tnr_guard = TNRSafetyGuard(regression_threshold=config.thresholds.regression_threshold)

    if not use_llm:
        console.print("  [yellow]Running in no-LLM mode (heuristic diagnosis only)[/yellow]\n")
    else:
        console.print(f"  [dim]Model: {llm_model} — Stage 2 AgentOrchestrator[/dim]\n")

    # Executa pipeline de agentes (logica migrada de agents.orchestrator)
    t0 = time.time()
    result: dict = {"success": False, "stages": {}}
    timeline: list[dict] = []

    def _transition(from_state: str, to_state: str, reason: str) -> None:
        timeline.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "from": from_state,
            "to": to_state,
            "reason": reason,
        })
        console.print(f"\n  [{to_state}] {reason}", style="bold")

    # 1. DETECTING
    _transition("IDLE", "DETECTING", "Analisando dados de observabilidade...")
    detection = detector.detect(report)
    result["stages"]["detection"] = detection

    if not detection.get("is_incident"):
        _transition("DETECTING", "IDLE", "Nenhum incidente detectado")
        result["timeline"] = timeline
        result["llm_model"] = llm_model if use_llm else "heuristic"
        console.print(f"\n  [dim]Agent pipeline completed in {time.time() - t0:.1f}s[/dim]")
        return result

    # 2. DIAGNOSING
    affected = ", ".join(detection.get("affected_services", []))
    _transition("DETECTING", "DIAGNOSING", f"Investigando incidente em {affected}")
    if use_llm:
        _outcome = _orchestrator.run(report, retriever)
        diagnosis = _outcome.diagnosis
        # Propaga atributos usados no REFLECT+WRITE do Estágio 1
        diagnoser.tools_used = _outcome.tools_used
        diagnoser.iteration_count = _outcome.iterations
    else:
        diagnosis = diagnoser._heuristic_diagnosis(report)
        diagnoser.tools_used = []
        diagnoser.iteration_count = 0
    result["stages"]["diagnosis"] = diagnosis

    if not diagnosis.get("root_cause"):
        _transition("DIAGNOSING", "FAILED", "Não foi possível identificar root cause")
        result["timeline"] = timeline
        result["llm_model"] = llm_model if use_llm else "heuristic"
        console.print(f"\n  [dim]Agent pipeline completed in {time.time() - t0:.1f}s[/dim]")
        return result

    # 3. MITIGATING
    _transition("DIAGNOSING", "MITIGATING", "Propondo remediação...")
    if use_llm:
        # Reutiliza mitigation já computada dentro do orchestrator (evita duplicata)
        mitigation = _outcome.mitigation
    else:
        mitigation = mitigator.propose(diagnosis)
    result["stages"]["mitigation"] = mitigation

    # 4. VERIFYING (TNR)
    _transition("MITIGATING", "VERIFYING", "Verificando impacto (TNR)...")
    if use_llm:
        # Reutiliza resultado TNR já computado dentro do orchestrator (evita duplicata)
        _tnr_regression = not _outcome.tnr_passed
        _tnr_msg = _outcome.tnr_message
        result["stages"]["verification"] = {
            "result": "success" if _outcome.tnr_passed else "regression",
            "health_delta": _outcome.tnr_health_delta,
            "message": _tnr_msg,
        }
    else:
        tnr_result = mitigator.verify_with_tnr(mitigation, tnr_guard)
        _tnr_regression = tnr_result.result.value == "regression"
        _tnr_msg = tnr_result.message
        result["stages"]["verification"] = {
            "result": tnr_result.result.value,
            "health_delta": tnr_result.health_delta,
            "message": _tnr_msg,
        }

    if _tnr_regression:
        _transition("VERIFYING", "ROLLBACK", f"Regressão detectada: {_tnr_msg}")
        result["stages"]["rollback"] = {"message": "Rollback executado, retornando ao diagnostico"}
    else:
        _transition("VERIFYING", "RESOLVED", f"Incidente resolvido: {_tnr_msg}")
        result["success"] = True

    # ── Skill Evolution: REFLECT + WRITE ─────────────────────────────────
    # Reflexão pós-TNR e persistência de outcome no SQLite.
    _tnr_str = result.get("stages", {}).get("verification", {}).get("result", "unknown")
    _tnr_passed = (_tnr_str == "success")

    # Fix 3: inicializa antes dos branches para evitar NameError em fluxos inesperados
    _reflection: dict = {"lesson": "no-llm mode", "score": 0.5, "priority_tools": []}
    if use_llm and _outcome.reflection:
        _reflection = _outcome.reflection
    elif use_llm:
        _reflection = diagnoser.reflect(
            diagnosis=diagnosis if diagnosis else {},
            tnr_result=_tnr_str,
            tools_used=diagnoser.tools_used,
        )
    if use_llm:
        result["skill_reflection"] = _reflection
        console.print(
            f"  [dim]Reflection: {_reflection.get('lesson', '—')} "
            f"(score={_reflection.get('score', 0.5):.2f})[/dim]"
        )

    # Persiste outcome independente do modo (write funciona em --no-llm)
    try:
        from rag.memory import SkillOutcome
        import datetime as _dt
        # Fix 2: renomeado para _skill_outcome — evita shadow sobre AgentOutcome
        _skill_outcome = SkillOutcome(
            outcome_id=str(uuid.uuid4()),
            skill_id=getattr(getattr(diagnoser, "skill", None), "skill_id", "default"),
            incident_id=str(uuid.uuid4()),
            tnr_passed=_tnr_passed,
            reflection_score=float(_reflection.get("score", 0.5)),
            reflection_lesson=str(_reflection.get("lesson", "")),
            tools_used=diagnoser.tools_used,
            iteration_count=diagnoser.iteration_count,
            timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        )
        _memory_db.save_skill_outcome(_skill_outcome)

        # ── Fase 5: EVOLVE — reescreve skills fracas ─────────────────────
        from agents.skill_evolver import SkillEvolver

        _active_skill = getattr(diagnoser, "skill", None)
        if _active_skill is not None:
            # Fix 6: usa client do orchestrator (diagnoser.client é None no Estágio 2)
            _evolver = SkillEvolver(
                client=_orchestrator.client if use_llm else None,
                memory_db=_memory_db,
            )
            if _evolver.should_evolve(_active_skill):
                _evolved = _evolver.evolve(_active_skill)
                if _evolved:
                    console.print(
                        f"  [dim]Skill evolved: {_evolved.name} → v{_evolved.version}[/dim]"
                    )
    except Exception:
        pass  # write/evolve é best-effort; não bloqueia o pipeline
    # ─────────────────────────────────────────────────────────────────────

    result["timeline"] = timeline
    result["llm_model"] = llm_model if use_llm else "heuristic"
    console.print(f"\n  [dim]Agent pipeline completed in {time.time() - t0:.1f}s[/dim]")
    return result


def step_safety_validation(result):
    """Etapa 4b: Pre-Execution Safety Validation."""
    console.print(Rule("[bold]Stage 4b: Pre-Execution Safety Validation[/bold]", style="cyan"))
    console.print()

    mitigation = result.get("stages", {}).get("mitigation", {})
    diagnosis = result.get("stages", {}).get("diagnosis", {})

    if not mitigation or not mitigation.get("action"):
        console.print("  [dim]No mitigation to validate (skipping)[/dim]\n")
        return None

    pre_validator = PreExecutionValidator(
        auto_approve_low_risk=True,
        max_blast_radius=3,
        require_rollback_plan=True,
    )
    orchestrator = SafetyOrchestrator(pre_validator=pre_validator)

    action = mitigation["action"]
    orch_result = orchestrator.validate(action, diagnosis)
    safety_result = orch_result.layer_details["pre_validator"]

    # Display results
    table = Table(title="Pre-Execution Safety Check", border_style="red" if not safety_result.is_safe else "green",
                  show_header=True)
    table.add_column("Check", style="bold")
    table.add_column("Result")

    # Risk level with color
    risk_colors = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red", "CRITICAL": "bold red"}
    risk_color = risk_colors.get(safety_result.risk_level.value, "white")
    table.add_row("Risk Level", f"[{risk_color}]{safety_result.risk_level.value}[/{risk_color}]")
    table.add_row("Risk Score", f"{safety_result.risk_score:.0f}/100")
    table.add_row("Approval", safety_result.approval_status.value)
    table.add_row("Requires Human", "Yes ⚠️" if safety_result.requires_human else "No ✅")

    # Blast radius
    br = safety_result.blast_radius
    table.add_row("Blast Radius", f"{br.scope} ({br.affected_services} services, ~{br.affected_pods_estimate} pods)")
    table.add_row("Reversible", "✅ Yes" if br.is_reversible else "❌ No")
    table.add_row("Data Destructive", "❌ Yes" if br.is_data_destructive else "✅ No")
    table.add_row("Est. Downtime", f"{br.estimated_downtime_seconds}s")
    console.print(table)

    # Checks passed/failed
    for check in safety_result.checks_passed:
        console.print(f"  ✅ {check}", style="green")
    for check in safety_result.checks_failed:
        console.print(f"  ❌ {check}", style="red")
    for warning in safety_result.warnings:
        console.print(f"  ⚠️  {warning}", style="yellow")

    # Recommendation
    console.print(f"\n  {safety_result.recommendation}\n")

    # Store in result
    result["stages"]["safety_validation"] = {
        "is_safe": safety_result.is_safe,
        "risk_level": safety_result.risk_level.value,
        "risk_score": safety_result.risk_score,
        "approval_status": safety_result.approval_status.value,
        "requires_human": safety_result.requires_human,
        "blast_radius_scope": br.scope,
        "recommendation": safety_result.recommendation,
    }

    return safety_result


def step_remediation_validation(result):
    """Etapa 4c: Valida versões e CVEs na remediação proposta."""
    console.print(Rule("[bold]Stage 4c: Remediation Version/CVE Validation[/bold]", style="cyan"))
    console.print()

    mitigation = result.get("stages", {}).get("mitigation", {})
    if not mitigation or not mitigation.get("action"):
        console.print("  [dim]No mitigation to validate (skipping)[/dim]\n")
        return None

    action = mitigation["action"]
    version_validator = VersionValidator()
    orchestrator = SafetyOrchestrator(version_validator=version_validator)

    t0 = time.time()
    orch_result = orchestrator.validate(action)
    validation = orch_result.layer_details["version_validator"]
    elapsed = time.time() - t0

    # Display results
    border_color = "green" if validation.is_safe else "red"
    table = Table(title="Remediation CVE/Version Check", border_style=border_color, show_header=True)
    table.add_column("Check", style="bold")
    table.add_column("Result")

    risk_colors = {"SAFE": "green", "CAUTION": "yellow", "DANGEROUS": "red", "MALICIOUS": "bold red"}
    risk_color = risk_colors.get(validation.risk.name, "white")
    table.add_row("Risk Level", f"[{risk_color}]{validation.risk.name}[/{risk_color}]")
    table.add_row("Is Safe", "✅ Yes" if validation.is_safe else "❌ No")

    if validation.checks:
        for check in validation.checks:
            status = "✅" if check.is_safe else "❌"
            cve_str = f" ({', '.join(check.known_cves[:2])})" if check.known_cves else ""
            table.add_row(
                f"Version: {check.package}",
                f"{status} {check.proposed_version}{cve_str}",
            )

    console.print(table)

    # Blocked patterns
    for blocked in validation.blocked_patterns:
        console.print(f"  ❌ {blocked}", style="red")
    for warning in validation.warnings:
        console.print(f"  ⚠️  {warning}", style="yellow")

    console.print(f"\n  {validation.recommendation}")
    console.print(f"  [dim]Validation time: {elapsed:.3f}s[/dim]\n")

    # Store in result
    result["stages"]["remediation_validation"] = {
        "is_safe": validation.is_safe,
        "risk": validation.risk.name,
        "blocked_patterns": validation.blocked_patterns,
        "warnings": validation.warnings,
        "recommendation": validation.recommendation,
    }

    return validation


# Mapa de action items por tipo de incidente — usado tanto no postmortem markdown quanto JSON.
ACTION_ITEMS_MAP = {
    "connection pool": [
        "Add connection pool size validation to CI/CD pipeline",
        "Implement config change canary deployment (gradual rollout)",
        "Add alerting for connection pool utilization > 80%",
        "Review and update runbook for connection pool incidents",
        "Add integration test for config changes affecting pool size",
    ],
    "memory": [
        "Add memory usage alerting and auto-scaling policies",
        "Investigate memory leak root cause in affected service",
        "Add heap dump collection to incident response runbook",
        "Review memory limits and resource quotas for affected services",
    ],
    "cpu": [
        "Add CPU usage alerting and auto-scaling policies",
        "Profile application for CPU hotspots under load",
        "Review resource limits and request quotas",
        "Add load testing to CI/CD pipeline",
    ],
    "timeout": [
        "Review and adjust timeout configurations across services",
        "Add circuit breaker patterns for inter-service calls",
        "Implement retry policies with exponential backoff",
        "Add timeout monitoring and alerting dashboards",
    ],
    "unknown": [
        "Investigate unidentified fault type and document findings",
        "Add monitoring for newly discovered failure pattern",
        "Escalate to engineering team for root cause analysis",
        "Create runbook entry for this incident type",
    ],
    "default": [
        "Review deployment process and add canary rollout",
        "Update incident response runbook with new pattern",
        "Add automated rollback triggers for similar incidents",
        "Implement pre-deployment validation checks",
    ],
}


def _get_action_items(pattern_matched: str) -> list[str]:
    """Retorna action items com base no tipo de incidente."""
    return ACTION_ITEMS_MAP.get(pattern_matched, ACTION_ITEMS_MAP["default"])


def _json_default(obj):
    """Encoder fallback para json.dumps — serializa tipos nao-nativos."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, enum.Enum):
        return obj.value
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, dict):
        return dict(obj)
    return str(obj)


def _serialize_health_snapshot(snapshot) -> dict:
    """Serializa HealthSnapshot incluindo a property health_score."""
    if snapshot is None:
        return {}
    d = dataclasses.asdict(snapshot)
    d["health_score"] = snapshot.health_score
    # datetime precisa de conversao
    if isinstance(d.get("timestamp"), datetime):
        d["timestamp"] = d["timestamp"].isoformat()
    return d


def _serialize_result(result: dict, incident) -> dict:
    """Converte result dict completo para estrutura JSON-safe.

    Monta dict com secoes: meta, detection, diagnosis, mitigation,
    safety_validation, remediation_validation, verification,
    action_items, timeline, config.
    """
    stages = result.get("stages", {})

    # Meta
    now = datetime.now()
    meta = {
        "generated_at": now.isoformat(),
        "scenario": result.get("scenario_name", "unknown"),
        "llm_model": result.get("llm_model", "unknown"),
        "config_source": result.get("config_source", "defaults"),
        "success": result.get("success", False),
        "duration_minutes": round(
            (incident.fix_time - incident.deploy_time).total_seconds() / 60, 1
        ),
    }

    # Detection
    detection = stages.get("detection", {})

    # Diagnosis
    diagnosis = stages.get("diagnosis", {})

    # Mitigation
    mitigation_raw = stages.get("mitigation", {})
    mitigation = {}
    if mitigation_raw:
        action = mitigation_raw.get("action")
        if action is not None:
            mitigation["action"] = dataclasses.asdict(action) if dataclasses.is_dataclass(action) else action
        mitigation["pattern_matched"] = mitigation_raw.get("pattern_matched", "default")

    # Safety validation
    safety_validation = stages.get("safety_validation", {})

    # Remediation validation
    remediation_validation = stages.get("remediation_validation", {})

    # Verification — com snapshots que possuem health_score property
    verification_raw = stages.get("verification", {})
    verification = {}
    if verification_raw:
        verification["result"] = verification_raw.get("result", "N/A")
        verification["health_delta"] = verification_raw.get("health_delta", "N/A")
        verification["message"] = verification_raw.get("message", "N/A")
        pre = verification_raw.get("pre_snapshot")
        post = verification_raw.get("post_snapshot")
        if pre is not None:
            verification["pre_snapshot"] = _serialize_health_snapshot(pre)
        if post is not None:
            verification["post_snapshot"] = _serialize_health_snapshot(post)

    # Action items
    pattern_matched = mitigation_raw.get("pattern_matched", "default") if mitigation_raw else "default"
    action_items = _get_action_items(pattern_matched)

    # Timeline
    timeline = {
        "incident_timeline": {
            "deploy_time": incident.deploy_time.isoformat(),
            "spike_start": incident.spike_start.isoformat(),
            "detection_time": incident.detection_time.isoformat(),
            "fix_time": incident.fix_time.isoformat(),
        },
        "state_transitions": result.get("timeline", []),
    }

    # Config
    config = {}
    tc = result.get("threshold_config")
    if tc is not None:
        config["thresholds"] = tc.thresholds.model_dump() if hasattr(tc, 'thresholds') else dataclasses.asdict(tc)
    config["source"] = result.get("config_source", "defaults")

    # Tracking (Story 6.3)
    tracking = stages.get("tracking", {})

    serialized = {
        "meta": meta,
        "detection": detection,
        "diagnosis": diagnosis,
        "mitigation": mitigation,
        "safety_validation": safety_validation,
        "remediation_validation": remediation_validation,
        "verification": verification,
        "action_items": action_items,
        "timeline": timeline,
        "config": config,
    }
    if tracking:
        serialized["tracking"] = tracking

    return serialized


def step_predict(result: dict, report) -> None:
    """Etapa 4d: Estágio preditivo — gera PredictionReport e emite eventos de lifecycle.

    Executa análise preditiva sobre o aggregated_report e armazena o resultado
    em result["stages"]["prediction"]. Emite evento de lifecycle se event_level
    for definido. Nunca lança exceção — falhas retornam prediction=None.
    """
    console.print(Rule("[bold]Stage 4d: Predictive Analysis[/bold]", style="cyan"))
    console.print()

    try:
        from pipeline.predictive_stage import run_predictive_stage
        from core.lifecycle import LifecycleEvent, ALLOWED_EVENT_TYPES
        from datetime import datetime, timezone
        import uuid

        prediction = run_predictive_stage(report)
        result["stages"]["prediction"] = prediction

        if prediction is not None:
            console.print(f"  Risk score: [bold]{prediction.risk_score:.1f}/100[/bold]")
            console.print(f"  Signals: {', '.join(prediction.signals) or 'none'}")
            if prediction.event_level:
                color = "red" if prediction.event_level == "prediction.alert" else "yellow"
                console.print(f"  Event: [{color}]{prediction.event_level}[/{color}]")

                # Emite evento de lifecycle
                event = LifecycleEvent(
                    event_type=prediction.event_level,
                    node_name="predictive_stage",
                    correlation_id=str(uuid.uuid4()),
                    timestamp=datetime.now(timezone.utc),
                    data={
                        "risk_score": prediction.risk_score,
                        "eta_minutes": prediction.eta_minutes,
                        "signals": prediction.signals,
                    },
                )
                console.print(f"  [dim]Lifecycle event emitted: {event.event_type}[/dim]")
            else:
                console.print("  [dim]No lifecycle event (risk below threshold)[/dim]")
        else:
            console.print("  [yellow]Predictive stage returned None (graceful degradation)[/yellow]")

    except Exception as exc:
        console.print(f"  [yellow]Predictive stage error (non-fatal): {exc}[/yellow]")
        result["stages"]["prediction"] = None

    console.print()


def step_postmortem(result, incident, output_dir: str = "output",
                    output_format: str = "markdown",
                    tracker_url: str | None = None,
                    tracker_issue_key: str | None = None):
    """Etapa 5: Gera postmortem em markdown e/ou JSON."""
    console.print(Rule("[bold]Stage 5: Postmortem Generation[/bold]", style="cyan"))
    console.print()

    diagnosis = result.get("stages", {}).get("diagnosis", {})
    mitigation = result.get("stages", {}).get("mitigation", {})
    verification = result.get("stages", {}).get("verification", {})

    scenario_name = result.get("scenario_name", "payment-pool-exhaustion")
    now = datetime.now()
    llm_model = result.get("llm_model", "unknown")
    postmortem = f"""# Postmortem - Incident {now.strftime('%Y-%m-%d')}

## Summary

- **Date**: {now.strftime('%Y-%m-%d %H:%M')}
- **Scenario**: {scenario_name}
- **LLM Model**: {llm_model}
- **Duration**: ~{(incident.fix_time - incident.deploy_time).total_seconds() / 60:.0f} minutes
- **Severity**: {result.get('stages', {}).get('detection', {}).get('severity', 'UNKNOWN')}
- **Impact**: Service degradation detected via automated analysis
- **Resolution**: {'Resolved' if result.get('success') else 'Requires manual intervention'}

## Root Cause

{diagnosis.get('root_cause', 'Unable to determine root cause')}

**Confidence**: {diagnosis.get('confidence', 'unknown')}

## Evidence

"""
    for i, ev in enumerate(diagnosis.get("evidence", []), 1):
        postmortem += f"{i}. {ev}\n"

    postmortem += f"""
## Timeline

| Time | Event |
|------|-------|
| {incident.deploy_time.strftime('%H:%M:%S')} | Deployment or configuration change applied |
| {incident.spike_start.strftime('%H:%M:%S')} | {diagnosis.get('root_cause', 'Anomaly detected')[:80]} |
| {incident.detection_time.strftime('%H:%M:%S')} | Automated detection triggered |
| {incident.fix_time.strftime('%H:%M:%S')} | Mitigation applied |

### State Machine Transitions

"""
    for t in result.get("timeline", []):
        postmortem += f"- **{t['time']}** {t['from']} -> {t['to']}: {t['reason']}\n"

    postmortem += f"""
## Mitigation Applied

"""
    if mitigation:
        action = mitigation.get("action")
        if action:
            postmortem += f"- **Action**: {action.description}\n"
            postmortem += f"- **Command**: `{action.command}`\n"
            postmortem += f"- **Risk Level**: {action.risk_level}\n"
            postmortem += f"- **Rollback**: `{action.rollback_command or 'N/A'}`\n"

    # Safety validation section
    safety_validation = result.get("stages", {}).get("safety_validation", {})
    if safety_validation:
        postmortem += f"""
## Pre-Execution Safety Check

- **Risk Level**: {safety_validation.get('risk_level', 'N/A')}
- **Risk Score**: {safety_validation.get('risk_score', 'N/A')}/100
- **Approval Status**: {safety_validation.get('approval_status', 'N/A')}
- **Requires Human**: {'Yes' if safety_validation.get('requires_human') else 'No'}
- **Blast Radius**: {safety_validation.get('blast_radius_scope', 'N/A')}
- **Recommendation**: {safety_validation.get('recommendation', 'N/A')}
"""

    # Remediation validation section
    remediation_validation = result.get("stages", {}).get("remediation_validation", {})
    if remediation_validation:
        postmortem += f"""
## Remediation CVE/Version Validation

- **Risk**: {remediation_validation.get('risk', 'N/A')}
- **Is Safe**: {'Yes' if remediation_validation.get('is_safe') else 'No'}
- **Blocked Patterns**: {', '.join(remediation_validation.get('blocked_patterns', [])) or 'None'}
- **Warnings**: {', '.join(remediation_validation.get('warnings', [])) or 'None'}
- **Recommendation**: {remediation_validation.get('recommendation', 'N/A')}
"""

    postmortem += f"""
## TNR Verification

- **Result**: {verification.get('result', 'N/A')}
- **Health Delta**: {verification.get('health_delta', 'N/A')}
- **Message**: {verification.get('message', 'N/A')}

## Action Items

"""

    # Gera action items dinâmicos com base no pattern_matched da mitigação.
    pattern_matched = mitigation.get("pattern_matched", "default") if mitigation else "default"
    items = _get_action_items(pattern_matched)
    for i, item in enumerate(items, 1):
        postmortem += f"{i}. [ ] {item}\n"

    # Incident Tracking section (Story 6.3)
    if tracker_url and tracker_issue_key:
        browse_url = f"{tracker_url}/browse/{tracker_issue_key}"
        postmortem += f"""
## Incident Tracking

- **Jira Issue**: [{tracker_issue_key}]({browse_url})

"""

    confidence = diagnosis.get("confidence", "unknown")
    postmortem += f"""
## Lessons Learned

- Automated detection identified the incident promptly
- Heuristic diagnosis provided {confidence}-confidence assessment
- TNR verification validated the mitigation outcome
- Incident response pipeline completed all stages successfully

---

*Generated by AIOps Agent - Agentic Incident Response System*
*Papers: STRATUS (NeurIPS 2025), OpsAgent (Nankai 2025), RCACopilot (EuroSys 2024), Acon (Microsoft 2025), AOI (Columbia 2025), TAMO (Shandong 2025), "When AIOps Become AI Oops" (RSAC Labs 2025)*
"""

    # Salva postmortem nos formatos solicitados
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    timestamp_str = now.strftime('%Y-%m-%d_%H%M%S')

    markdown_path = None
    json_path = None

    # Markdown output
    if output_format in ("markdown", "both"):
        markdown_path = out / f"postmortem_{timestamp_str}.md"
        markdown_path.write_text(postmortem)
        console.print(f"  Postmortem (markdown) saved to: [bold]{markdown_path}[/bold]")

    # JSON output
    if output_format in ("json", "both"):
        serialized = _serialize_result(result, incident)
        json_path = out / f"postmortem_{timestamp_str}.json"
        json_path.write_text(json.dumps(serialized, default=_json_default, indent=2, ensure_ascii=False))
        console.print(f"  Postmortem (JSON) saved to: [bold]{json_path}[/bold]")

    # Preview (mostra markdown se disponivel)
    if output_format in ("markdown", "both"):
        console.print()
        console.print(Panel(
            Markdown(postmortem[:1500] + "\n\n*... (truncated for display)*"),
            title="Postmortem Preview",
            border_style="dim",
        ))

    return markdown_path, json_path


def print_summary(result, compression_stats=None, safety_result=None,
                   san_report=None, remediation_result=None, tracker_info=None):
    """Exibe resumo final."""
    console.print()
    console.print(Rule("[bold]Summary[/bold]", style="cyan"))
    console.print()

    success = result.get("success", False)
    status_icon = "[bold green]RESOLVED[/bold green]" if success else "[bold red]UNRESOLVED[/bold red]"

    table = Table(title="Incident Response Summary", border_style="cyan")
    table.add_column("Stage", style="bold")
    table.add_column("Result")
    table.add_column("Details")

    # Scenario
    scenario_name = result.get("scenario_name", "payment-pool-exhaustion")
    table.add_row("Scenario", f"[bold]{scenario_name}[/bold]", "")

    # Thresholds
    config_source = result.get("config_source", "defaults")
    tc = result.get("threshold_config")
    if tc:
        table.add_row(
            "Thresholds",
            f"[dim]σ={tc.thresholds.sigma_threshold:.1f}, p{tc.thresholds.percentile_threshold:.0f}, TNR={tc.thresholds.regression_threshold:.1f}[/dim]",
            f"[dim]source: {config_source}[/dim]",
        )

    # Telemetry Sanitization (AIOpsShield)
    if san_report:
        if san_report.threat_detected:
            table.add_row(
                "Sanitization",
                f"[bold red]🛡️ {san_report.adversarial_entries} threats blocked[/bold red]",
                f"{san_report.total_entries} entries, {san_report.fields_sanitized} fields sanitized",
            )
        else:
            table.add_row(
                "Sanitization",
                "[green]✅ clean[/green]",
                f"{san_report.total_entries} entries analyzed, no threats",
            )

    # Detection
    detection = result.get("stages", {}).get("detection", {})
    table.add_row(
        "Detection",
        "[green]Incident detected[/green]" if detection.get("is_incident") else "[yellow]No incident[/yellow]",
        f"{len(detection.get('signals', []))} correlated signals",
    )

    # Context Compression (Acon + AOI)
    if compression_stats:
        ratio = max(compression_stats.compression_ratio, 0)
        # critical_preserved can be 100.0 (meaning 100%) — normalize
        crit_pres = min(compression_stats.critical_preserved, 100) / 100
        table.add_row(
            "Compression",
            f"[magenta]{ratio:.0%} reduction[/magenta]" if ratio > 0 else "[magenta]within budget[/magenta]",
            f"{compression_stats.chunks_kept} chunks kept, {compression_stats.chunks_removed} removed, "
            f"{crit_pres:.0%} critical preserved",
        )

    # Diagnosis
    diagnosis = result.get("stages", {}).get("diagnosis", {})
    llm_model = result.get("llm_model", "unknown")
    table.add_row(
        "Diagnosis",
        f"[green]{diagnosis.get('confidence', '?')} confidence[/green]",
        f"{diagnosis.get('root_cause', 'N/A')[:60]} [dim](model: {llm_model})[/dim]",
    )

    # Mitigation
    mitigation = result.get("stages", {}).get("mitigation", {})
    if mitigation:
        pattern = mitigation.get("pattern_matched", "N/A")
        table.add_row("Mitigation", f"Pattern: {pattern}", "Awaiting human approval")

    # Safety Validation (Pre-Validator)
    if safety_result:
        risk_colors = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red", "CRITICAL": "bold red"}
        risk_color = risk_colors.get(safety_result.risk_level.value, "white")
        table.add_row(
            "Safety Check",
            f"[{risk_color}]{safety_result.risk_level.value} risk ({safety_result.risk_score:.0f}/100)[/{risk_color}]",
            f"{safety_result.approval_status.value} | "
            f"{'requires human ⚠️' if safety_result.requires_human else 'auto-approved ✅'}",
        )

    # Remediation Validation (VersionValidator)
    if remediation_result:
        rem_colors = {"SAFE": "green", "CAUTION": "yellow", "DANGEROUS": "red", "MALICIOUS": "bold red"}
        rem_color = rem_colors.get(remediation_result.risk.name, "white")
        table.add_row(
            "CVE/Version",
            f"[{rem_color}]{remediation_result.risk.name}[/{rem_color}]",
            f"{'safe ✅' if remediation_result.is_safe else 'blocked ❌'} | "
            f"{len(remediation_result.blocked_patterns)} blocked, {len(remediation_result.warnings)} warnings",
        )

    # Predictive Analysis
    prediction = result.get("stages", {}).get("prediction")
    if prediction is not None:
        risk_color = "red" if prediction.risk_score >= 60 else "yellow" if prediction.risk_score >= 30 else "green"
        event_str = prediction.event_level or "none"
        table.add_row(
            "Prediction",
            f"[{risk_color}]risk={prediction.risk_score:.1f}/100[/{risk_color}]",
            f"event={event_str} | signals={', '.join(prediction.signals) or 'none'}",
        )

    # Verification
    verification = result.get("stages", {}).get("verification", {})
    if verification:
        tnr_result = verification.get("result", "N/A")
        style = "green" if tnr_result == "success" else "red"
        table.add_row("TNR Verify", f"[{style}]{tnr_result}[/{style}]", verification.get("message", "")[:80])

    # Incident Tracking (Story 6.3)
    if tracker_info:
        issue_key = tracker_info.get("issue_key")
        tracker_url = tracker_info.get("url", "")
        if issue_key:
            table.add_row(
                "Tracking",
                f"[green]{issue_key}[/green]",
                f"{tracker_url}/browse/{issue_key}" if tracker_url else issue_key,
            )
        else:
            table.add_row(
                "Tracking",
                "[yellow]skipped[/yellow]",
                "tracker error — postmortem still generated",
            )

    table.add_row("Status", status_icon, f"{len(result.get('timeline', []))} state transitions")
    console.print(table)

    # Timeline visual
    console.print("\n  [bold]State Machine Timeline:[/bold]")
    for t in result.get("timeline", []):
        icon = {"DETECTING": "->", "DIAGNOSING": "->", "MITIGATING": "->",
                "VERIFYING": "->", "RESOLVED": "->", "ROLLBACK": "<-", "FAILED": "!!"}.get(t["to"], "->")
        console.print(f"    {t['time']} {icon} {t['from']} -> [bold]{t['to']}[/bold]: {t['reason'][:60]}")


def build_arg_parser() -> argparse.ArgumentParser:
    """Cria o parser de argumentos CLI.

    Separado de main() para permitir testes sem executar o pipeline.
    """
    parser = argparse.ArgumentParser(
        description="AIOps Agent - Agentic Incident Response Demo",
        epilog="""Exit codes:
  0  Pipeline resolved successfully (TNR verified)
  1  Unrecoverable error (exception in any stage)
  2  TNR regression detected (post-health worse than pre-health)
  3  Safety blocked action (human approval required)""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM calls (heuristic mode)")
    parser.add_argument("--compact", action="store_true", help="Compact output")
    parser.add_argument("--no-rag", action="store_true", help="Skip RAG indexing (faster)")
    parser.add_argument("--pipeline-v1", action="store_true", help="Use Blueprint Engine (V1) pipeline")
    parser.add_argument(
        "--scenario",
        choices=list_scenarios(),
        default=None,
        metavar="NAME",
        help="Incident scenario to run (default: payment-pool-exhaustion). Choices: %(choices)s",
    )
    parser.add_argument(
        "--data-source",
        choices=["simulated", "databricks"],
        default="simulated",
        metavar="SOURCE",
        help="Data source for observability data (default: simulated). Choices: simulated, databricks",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output",
        metavar="DIR",
        help="Directory for postmortem output files (default: output/)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        metavar="FILE",
        help="Path to YAML config file for analysis thresholds (default: config/defaults.yaml)",
    )
    parser.add_argument(
        "--output-format",
        choices=["markdown", "json", "both"],
        default="markdown",
        metavar="FORMAT",
        help="Postmortem output format: markdown (default), json, or both",
    )
    parser.add_argument(
        "--rebuild-rag",
        action="store_true",
        help="Force RAG index reconstruction, ignoring any cached index",
    )
    parser.add_argument(
        "--tracker",
        choices=list(TRACKER_REGISTRY.keys()) + ["none"],
        default=None,
        metavar="TRACKER",
        help=(
            "Incident tracker for auto-creating issues after diagnosis. "
            "Choices: jira, none. Default: omitted (no tracking, backward-compatible). "
            "'none' explicitly disables tracking."
        ),
    )
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Valida regras de exclusao mutua entre argumentos CLI.

    Regras:
      --data-source databricks + --scenario sao mutuamente exclusivos.
      Dados reais nao possuem cenarios simulados.
    """
    if args.data_source == "databricks" and args.scenario is not None:
        parser.error("--data-source databricks cannot be combined with --scenario (real data has no simulated scenarios)")


def _create_databricks_sources():
    """Factory que cria conectores Databricks a partir de env vars.

    Requer DATABRICKS_HOST, DATABRICKS_TOKEN e DATABRICKS_WAREHOUSE_ID.
    Retorna tupla (log_source, metrics_source, trace_source) ou None
    se alguma env var estiver ausente.
    """
    host = os.environ.get("DATABRICKS_HOST")
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")

    missing = []
    if not host:
        missing.append("DATABRICKS_HOST")
    if not token:
        missing.append("DATABRICKS_TOKEN")
    if not warehouse_id:
        missing.append("DATABRICKS_WAREHOUSE_ID")

    if missing:
        console.print(f"[red]Missing required env vars for Databricks: {', '.join(missing)}[/red]")
        console.print("[dim]Set them in .env or export before running.[/dim]")
        return None

    # Lazy import — NFR19: simulated mode nao deve importar este modulo
    from tools.databricks_connector import (
        DatabricksLogSource,
        DatabricksMetricsSource,
        DatabricksTraceSource,
    )

    log_source = DatabricksLogSource(host=host, token=token, warehouse_id=warehouse_id)
    metrics_source = DatabricksMetricsSource(host=host, token=token, warehouse_id=warehouse_id)
    trace_source = DatabricksTraceSource(host=host, token=token, warehouse_id=warehouse_id)

    return log_source, metrics_source, trace_source


# --- Tracker Registry (Story 6.3) ---

def _create_jira_tracker():
    """Factory que cria JiraIncidentTracker a partir de env vars.

    Lazy-import para evitar carregar requests quando tracker nao e usado.
    Retorna instancia de JiraIncidentTracker ou None se configuracao ausente.
    """
    try:
        from tools.jira_tracker import JiraIncidentTracker
        return JiraIncidentTracker()
    except Exception as e:
        console.print(f"[yellow]Warning: Could not initialize Jira tracker: {e}[/yellow]")
        return None


TRACKER_REGISTRY: dict[str, object] = {
    "jira": _create_jira_tracker,
}


def _resolve_tracker(tracker_name: str | None):
    """Resolve nome de tracker para instancia de IncidentTracker.

    Retorna None se tracker_name e None ou "none".
    Retorna instancia do tracker se factory funcionar, ou None com warning.
    """
    if tracker_name is None or tracker_name == "none":
        return None

    factory = TRACKER_REGISTRY.get(tracker_name)
    if factory is None:
        console.print(f"[yellow]Warning: Unknown tracker '{tracker_name}'. Skipping.[/yellow]")
        return None

    return factory()


def step_tracking(result, tracker) -> str | None:
    """Etapa 5b: Cria issue no tracker externo apos diagnostico.

    Extrai titulo, severidade e descricao do resultado do diagnostico
    e cria um incidente no tracker. Erros nao interrompem o pipeline.
    Retorna issue_key ou None se falhar.
    """
    console.print(Rule("[bold]Stage 5b: Incident Tracking[/bold]", style="cyan"))
    console.print()

    diagnosis = result.get("stages", {}).get("diagnosis", {})
    detection = result.get("stages", {}).get("detection", {})

    root_cause = diagnosis.get("root_cause", "AIOps detected incident")
    severity = detection.get("severity", "medium")
    evidence = diagnosis.get("evidence", [])

    # Titulo: primeiros 100 chars do root cause
    title = root_cause[:100] if len(root_cause) > 100 else root_cause

    # Descricao: root cause completo + evidencias
    description_parts = [f"Root Cause: {root_cause}", "", "Evidence:"]
    for ev in evidence:
        description_parts.append(f"- {ev}")
    description = "\n".join(description_parts)

    try:
        issue_key = tracker.create_incident(title, severity, description)
        console.print(f"  Tracked as: [bold]{issue_key}[/bold]")
        console.print(f"  URL: [dim]{tracker.url}/browse/{issue_key}[/dim]")
        return issue_key
    except Exception as e:
        console.print(f"[yellow]  Warning: Tracker incident creation failed: {e}[/yellow]")
        console.print("[dim]  Pipeline continues — postmortem will still be generated.[/dim]")
        return None


def _attach_postmortem_to_tracker(tracker, issue_key: str, result) -> str | None:
    """Anexa postmortem como comentario no tracker.

    Extrai timeline, root cause e action items do resultado do pipeline
    e cria comentario de postmortem no issue existente. Erros nao interrompem.
    Retorna comment_id ou None se falhar.
    """
    diagnosis = result.get("stages", {}).get("diagnosis", {})
    mitigation = result.get("stages", {}).get("mitigation", {})

    root_cause = diagnosis.get("root_cause", "Unable to determine root cause")

    # Mapeia timeline do pipeline para formato do tracker
    timeline = []
    for entry in result.get("timeline", []):
        timeline.append({
            "time": entry.get("time", "?"),
            "event": f"{entry.get('from', '?')} -> {entry.get('to', '?')}: {entry.get('reason', '')}",
        })

    # Action items do pattern matched
    pattern_matched = mitigation.get("pattern_matched", "default") if mitigation else "default"
    action_items = _get_action_items(pattern_matched)

    try:
        comment_id = tracker.create_postmortem(issue_key, timeline, root_cause, action_items)
        console.print(f"  Postmortem attached to {issue_key} (comment: {comment_id})")
        return comment_id
    except Exception as e:
        console.print(f"[yellow]  Warning: Postmortem attachment failed: {e}[/yellow]")
        return None


def step_fetch_databricks_data(
    log_source, metrics_source, trace_source,
) -> tuple[SimulatedIncident, str]:
    """Etapa 1 (Databricks): Busca dados reais via conectores.

    Cada conector e chamado individualmente com try/except para fallback
    gracioso — falha em um nao impede os outros.

    Args:
        log_source: Conector de logs (LogSource).
        metrics_source: Conector de metricas (MetricsSource).
        trace_source: Conector de traces (TraceSource).

    Returns:
        Tupla (SimulatedIncident com dados reais, "databricks-live").
    """
    import uuid as _uuid
    from data.models import LogEntry, MetricPoint, Span

    console.print(Rule("[bold]Stage 1: Databricks Data Fetch[/bold]", style="cyan"))
    console.print()

    now = datetime.now()

    # --- Logs ---
    logs: list[LogEntry] = []
    try:
        raw_logs = log_source.search(query="*", service="", start=None, end=None)
        for d in raw_logs:
            ts = datetime.fromisoformat(d["timestamp"]) if d.get("timestamp") else now
            logs.append(LogEntry(
                timestamp=ts,
                level=d.get("level", "INFO"),
                service=d.get("service", "unknown"),
                message=d.get("message", ""),
            ))
        console.print(f"  [green]Logs:[/green] {len(logs)} entries fetched")
    except Exception as exc:
        console.print(f"  [yellow]Logs:[/yellow] fetch failed ({exc}), using empty list")

    # --- Metrics ---
    metrics: list[MetricPoint] = []
    try:
        raw_metrics = metrics_source.query(promql="", start=None, end=None)
        for d in raw_metrics:
            ts = datetime.fromisoformat(d["timestamp"]) if d.get("timestamp") else now
            metrics.append(MetricPoint(
                timestamp=ts,
                service=d.get("service", "unknown"),
                metric_name=d.get("metric", ""),
                value=float(d.get("value", 0.0)),
            ))
        console.print(f"  [green]Metrics:[/green] {len(metrics)} points fetched")
    except Exception as exc:
        console.print(f"  [yellow]Metrics:[/yellow] fetch failed ({exc}), using empty list")

    # --- Traces ---
    traces: list[Span] = []
    try:
        raw_traces = trace_source.find_traces(service="", min_duration_ms=0.0)
        for d in raw_traces:
            traces.append(Span(
                trace_id=d.get("trace_id", ""),
                span_id=str(_uuid.uuid4()),
                parent_span_id=None,
                service=d.get("service", "unknown"),
                operation=d.get("operation", ""),
                start_time=now,
                duration_ms=float(d.get("duration_ms", 0.0)),
                status=d.get("status", "unknown"),
                attributes=d.get("attributes", {}),
            ))
        console.print(f"  [green]Traces:[/green] {len(traces)} spans fetched")
    except Exception as exc:
        console.print(f"  [yellow]Traces:[/yellow] fetch failed ({exc}), using empty list")

    incident = SimulatedIncident(
        logs=logs,
        metrics=metrics,
        traces=traces,
        deploy_time=now,
        spike_start=now,
        detection_time=now,
        fix_time=now,
        actual_root_cause="unknown (live data)",
        actual_fix="unknown (live data)",
    )
    console.print()
    return incident, "databricks-live"


def create_incident_state(
    args: argparse.Namespace,
    incident_data: dict,
) -> "IncidentState":
    """Cria IncidentState a partir de dados simulados e flags da CLI.

    Args:
        args: Argumentos da CLI.
        incident_data: Dicionario com logs, metrics, traces do simulador.

    Returns:
        IncidentState pronto para o pipeline.
    """
    import uuid
    from datetime import timezone
    from core.models import IncidentState, NormalizedAlert

    now = datetime.now(timezone.utc)
    correlation_id = f"inc-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

    alert = NormalizedAlert(
        source="simulated",
        source_id="sim-001",
        correlation_id=correlation_id,
        title="Simulated incident from data/sample_incident.py",
        description="Payment service connection pool exhaustion after config deploy",
        severity="critical",
        alert_type="HighErrorRate",
        timestamp=now,
        detection_timestamp=now,
        service="payment-svc",
        environment="production",
    )

    return IncidentState(
        incident_id=correlation_id,
        correlation_id=correlation_id,
        alert=alert,
        received_at=now,
        started_at=now,
        no_llm=args.no_llm,
        no_rag=getattr(args, "no_rag", False),
        metadata={
            "raw_incident_data": incident_data,
            "scenario": getattr(args, "scenario", None) or "payment-pool-exhaustion",
            "output_format": getattr(args, "output_format", "markdown"),
            "compact": getattr(args, "compact", False),
        },
    )


def display_results(state: "IncidentState", compact: bool = False) -> None:
    """Exibe resultados do pipeline Blueprint Engine via rich.

    Preserva o formato visual de V0 (Panel, Table, etc).
    """
    # Diagnostico
    diagnosis = state.diagnosis or {}
    if diagnosis:
        console.print(Panel(
            f"[bold]Root Cause:[/bold] {diagnosis.get('root_cause', 'Unknown')}\n"
            f"[bold]Confidence:[/bold] {diagnosis.get('confidence', 'unknown')}\n"
            f"[bold]Evidence:[/bold] {', '.join(diagnosis.get('evidence', [])[:3])}",
            title="Diagnosis Result",
            border_style="green",
        ))

    # Safety
    if state.safety_verdict:
        style = "green" if state.safety_verdict == "APPROVED" else "red"
        console.print(Panel(
            f"[bold]Verdict:[/bold] {state.safety_verdict}\n"
            f"[bold]Reasons:[/bold] {'; '.join(state.safety_reasons)}",
            title="Safety Verification",
            border_style=style,
        ))

    # Postmortem
    if state.postmortem_md and not compact:
        console.print(Rule("[bold]Postmortem[/bold]", style="cyan"))
        console.print(state.postmortem_md)


def _save_postmortem_v1(postmortem_md: str, output_dir: str = "output") -> Path | None:
    """Salva postmortem em arquivo markdown."""
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    filename = f"postmortem_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.md"
    path = output_path / filename
    path.write_text(postmortem_md)
    console.print(f"[green]Postmortem saved: {path}[/green]")
    return path


def main_v1(args: argparse.Namespace) -> int:
    """Execucao via Blueprint Engine (V1).

    Composition root para modo CLI usando o Blueprint Engine.
    Substitui as chamadas step_*() sequenciais pelo pipeline tipado.
    """
    import asyncio
    import os
    from pathlib import Path as _Path
    from core.logging import configure_logging, get_logger
    from core.store import InMemoryStore
    from core.credentials import EnvCredentialProvider
    from config.loader import load_config as load_app_config
    from blueprint.engine import BlueprintEngine, PipelineAborted, LLMCallLimitExceeded
    from blueprint.pipelines.incident_response import build_incident_response_pipeline

    # Carrega .env.local se existir (sem dependencia de python-dotenv)
    _env_file = _Path(".env.local")
    if _env_file.exists():
        for _line in _env_file.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

    log = get_logger("main")

    # 1. Configuracao e logging
    app_config = load_app_config(args.config)
    configure_logging()

    log.info("main.started", no_llm=args.no_llm, no_rag=args.no_rag, compact=args.compact)

    # Slack notifier — opcional, wired se SLACK_BOT_TOKEN estiver no ambiente
    from core.lifecycle import LifecycleEvent
    from datetime import timezone as _tz
    from tools.slack_setup import setup_slack_notifier
    _slack_notifier = setup_slack_notifier()
    if _slack_notifier is not None:
        _slack_channel = os.environ.get("SLACK_CHANNEL", "").lstrip("#")
        log.info("slack.notifier_created", channel=_slack_channel)
        console.print(f"[cyan]Slack: notifier created for #{_slack_channel}[/cyan]")

    def _fire(event_type: str, node_name: str | None = None, data: dict | None = None, cid: str = "") -> None:
        """Dispara evento de ciclo de vida para o SlackNotifier (sync wrapper)."""
        if _slack_notifier is None:
            return
        evt = LifecycleEvent(
            event_type=event_type,
            node_name=node_name,
            correlation_id=cid,
            timestamp=datetime.now(_tz.utc),
            data=data or {},
        )
        try:
            asyncio.run(_slack_notifier.on_event(evt))
            console.print(f"[cyan]Slack: {event_type} fired[/cyan]")
        except Exception as _exc:
            console.print(f"[yellow]Slack: {event_type} failed: {_exc}[/yellow]")
            log.warning("slack.fire_failed", event_type=event_type, error=str(_exc))

    # 2. Gera dados simulados
    if args.scenario:
        incident = get_scenario(args.scenario)(seed=42)
    else:
        incident = create_incident()

    incident_data = {
        "logs": incident.logs,
        "metrics": incident.metrics,
        "traces": incident.traces,
    }

    # 3. Composicao de dependencias
    store = InMemoryStore()
    creds = EnvCredentialProvider()
    engine = BlueprintEngine(app_config, store, creds)

    # 4. Constroi pipeline e estado inicial
    pipeline = build_incident_response_pipeline(app_config)
    state = create_incident_state(args, incident_data)

    # 5. RAG (se habilitado)
    if not args.no_rag:
        try:
            console.print(Rule("[bold]Stage 3: RAG Indexing[/bold]", style="cyan"))
            indexer = ObservabilityIndexer()
            # Indexa apos aggregator ter processado — por ora, sem report pre-pipeline
            retriever = ObservabilityRetriever(indexer)

            # Injeta retriever no metadata do estado
            state = state.model_copy(update={
                "metadata": {**state.metadata, "_retriever": retriever},
            })
        except Exception as exc:
            console.print(f"[yellow]RAG skipped: {exc}[/yellow]")

    # 6. Executa pipeline
    start_time = time.monotonic()
    exit_code = EXIT_SUCCESS

    _cid = state.correlation_id
    _fire("pipeline.started", cid=_cid, data={
        "title": state.alert.title if state.alert else "AIOps Incident",
        "severity": state.alert.severity if state.alert else "unknown",
        "service": state.alert.service if state.alert else "unknown",
    })

    try:
        console.print(Rule("[bold]Pipeline Execution (Blueprint Engine V1)[/bold]", style="cyan"))
        state = engine.run(pipeline, state)
    except PipelineAborted as exc:
        log.error("main.pipeline_aborted", error=str(exc))
        console.print(f"[red]Pipeline aborted: {exc}[/red]")
        _fire("pipeline.failed", cid=_cid, data={"error": str(exc)})
        if "regression" in str(exc).lower():
            exit_code = EXIT_REGRESSION
        elif "safety" in str(exc).lower() or "blocked" in str(exc).lower():
            exit_code = EXIT_APPROVAL_NEEDED
        else:
            exit_code = EXIT_ERROR
    except LLMCallLimitExceeded as exc:
        log.error("main.llm_limit_exceeded", error=str(exc))
        console.print(f"[red]LLM call limit exceeded: {exc}[/red]")
        _fire("pipeline.failed", cid=_cid, data={"error": str(exc)})
        exit_code = EXIT_ERROR
    else:
        # Pipeline completou com sucesso
        diagnosis = state.diagnosis or {}
        _conf_v1 = diagnosis.get("confidence", 0.0)
        _fire("pipeline.completed", cid=_cid, data={
            "root_cause": diagnosis.get("root_cause", ""),
            "confidence": float(_conf_v1) if isinstance(_conf_v1, (int, float)) else 0.0,
            "proposed_fix": diagnosis.get("proposed_fix") or diagnosis.get("fix_command", ""),
            "risk_level": state.safety_verdict or "unknown",
            "evidence": diagnosis.get("evidence", []),
            "total_duration_ms": 0,  # preenchido abaixo
        })

    elapsed = time.monotonic() - start_time

    # 7. Exibe resultados
    display_results(state, compact=args.compact)

    # 8. Salva postmortem
    output_dir = getattr(args, "output_dir", "output")
    if state.postmortem_md:
        _save_postmortem_v1(state.postmortem_md, output_dir=output_dir)

    # 9. Resumo final
    console.print(Rule("[bold]Summary[/bold]", style="cyan"))
    console.print(f"Total time: {elapsed:.1f}s")
    console.print(f"Exit code: {exit_code} ({EXIT_CODE_LABELS.get(exit_code, 'unknown')})")

    log.info("main.completed", exit_code=exit_code, duration_seconds=round(elapsed, 1))

    return exit_code


def get_pipeline(args: argparse.Namespace):
    """Factory: retorna a implementação de PipelineProtocol selecionada.

    Seleciona V1 (Blueprint Engine) se --pipeline-v1 ou data_source=databricks.
    Retorna V0 (step_* sequential pipeline) por padrão.

    Args:
        args: Namespace com flags de execução.

    Returns:
        CallablePipelineRunner que satisfaz PipelineProtocol.
    """
    from core.pipeline_protocol import make_pipeline

    use_v1 = getattr(args, "pipeline_v1", False)
    if use_v1:
        return make_pipeline(main_v1)
    return make_pipeline(_run_v0)


def _run_v0(args: argparse.Namespace) -> int:
    """Executa o pipeline V0 sequencial (step_* functions).

    No fluxo normal, o V0 é executado diretamente em main() quando
    --pipeline-v1 não está ativo. Esta função é o wrapper que satisfaz
    PipelineProtocol para composição programática fora de main().
    """
    # Para uso programático (testes/composição), retorna EXIT_SUCCESS.
    # Para execução via CLI, main() executa V0 inline após get_pipeline().
    return EXIT_SUCCESS


def main() -> int:
    """Entry point principal. Retorna exit code baseado no resultado do pipeline.

    Usa Blueprint Engine V1 para execucao simulated.
    Mantém path V0 para Databricks e tracker (funcionalidades nao migradas).
    """
    parser = build_arg_parser()
    args = parser.parse_args()

    # Validate mutual exclusion rules
    _validate_args(parser, args)

    # Load .env e .env.local
    load_dotenv()
    _env_local = Path(".env.local")
    if _env_local.exists():
        for _ln in _env_local.read_text().splitlines():
            _ln = _ln.strip()
            if _ln and not _ln.startswith("#") and "=" in _ln:
                _ek, _, _ev = _ln.partition("=")
                os.environ.setdefault(_ek.strip(), _ev.strip())

    # Verifica API key se vai usar LLM
    use_llm = not args.no_llm
    if use_llm and not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[yellow]ANTHROPIC_API_KEY not found. Running in heuristic mode (--no-llm).[/yellow]")
        console.print("[dim]Set ANTHROPIC_API_KEY in .env for full ReAct diagnosis.[/dim]\n")
        use_llm = False
        args.no_llm = True

    # Roteamento via factory — elimina condicionais inline de seleção de pipeline
    pipeline = get_pipeline(args)
    if getattr(args, "pipeline_v1", False):
        return pipeline.run(args)

    print_banner()

    # Slack notifier — opcional, sem impacto no pipeline se falhar
    import asyncio as _asyncio
    from core.lifecycle import LifecycleEvent as _LifecycleEvent
    from datetime import timezone as _tz2
    from tools.slack_setup import setup_slack_notifier as _setup_slack
    _notifier = _setup_slack()
    if _notifier is not None:
        _slack_ch = os.environ.get("SLACK_CHANNEL", "").lstrip("#")
        console.print(f"[cyan]Slack: notificacoes ativas em #{_slack_ch}[/cyan]")

    def _slack_fire(event_type: str, data: dict | None = None, cid: str = "unknown") -> None:
        if _notifier is None:
            return
        evt = _LifecycleEvent(
            event_type=event_type,
            node_name=None,
            correlation_id=cid,
            timestamp=datetime.now(_tz2.utc),
            data=data or {},
        )
        try:
            _asyncio.run(_notifier.on_event(evt))
        except Exception as _fe:
            console.print(f"[yellow]Slack: falha ao enviar {event_type}: {_fe}[/yellow]")

    # Blueprint Engine V1 esta disponivel via main_v1(args).
    # O path V0 e mantido como default para compatibilidade com testes existentes.
    # Para usar o Blueprint Engine diretamente, chame main_v1(args).

    # Leitura do modelo LLM configuravel via env var (Story 5.1)
    llm_model = os.environ.get("AIOPS_LLM_MODEL", "claude-sonnet-4-20250514")

    # Carrega configuracao via YAML (Pydantic Settings)
    threshold_config = load_config(config_path=args.config)
    config_source = args.config or "config/defaults.yaml"

    # Resolve tracker (Story 6.3) — antes do pipeline para falhar rapido em config invalida
    tracker = _resolve_tracker(args.tracker)

    import uuid as _uuid
    _pipeline_cid = f"inc-{datetime.now().strftime('%Y%m%d')}-{_uuid.uuid4().hex[:8]}"

    try:
        # === Pipeline ===
        t_total = time.time()

        _slack_fire("pipeline.started", cid=_pipeline_cid, data={
            "title": f"AIOps: {args.scenario or 'payment-pool-exhaustion'}",
            "severity": "critical",
            "service": "payment-svc",
        })

        # 1. Data Generation / Fetch
        if args.data_source == "databricks":
            sources = _create_databricks_sources()
            if sources is None:
                console.print("[red]Cannot proceed without Databricks connection. Exiting.[/red]")
                return EXIT_ERROR
            log_source, metrics_source, trace_source = sources
            incident, scenario_name = step_fetch_databricks_data(
                log_source, metrics_source, trace_source,
            )
        else:
            incident, scenario_name = step_generate_data(scenario_name=args.scenario)

        # 1b. Telemetry Sanitization (AIOpsShield)
        san_report = step_sanitize(incident)

        # 2. Pre-Processing (now operates on sanitized data)
        report = step_pipeline(incident, config=threshold_config)

        # 2b. Context Compression
        compressor, compressed_text, compression_stats = step_compression(report)

        # 2c. Predictive Stage (no-op se não disponível)
        try:
            _mem_db = IncidentMemoryDB()
            step_predict(report, memory_db=_mem_db)
        except Exception:
            pass

        # 3. RAG Indexing
        if not args.no_rag:
            retriever = step_rag(report, rebuild=args.rebuild_rag)
        else:
            if args.rebuild_rag:
                console.print("[yellow]Warning: --rebuild-rag ignored because --no-rag is set[/yellow]")
            console.print(Rule("[bold]Stage 3: RAG Indexing[/bold] [dim](skipped)[/dim]", style="cyan"))
            console.print()
            retriever = None

        # 4. Agent Pipeline
        result = step_agents(report, retriever, use_llm=use_llm, llm_model=llm_model,
                             config=threshold_config)

        # 4d. Predictive Analysis
        step_predict(result, report)

        # 4b. Pre-Execution Safety Validation
        safety_result = step_safety_validation(result)

        # 4c. Remediation Version/CVE Validation
        remediation_result = step_remediation_validation(result)

        # Armazena nome do cenário e config no resultado para postmortem e summary.
        result["scenario_name"] = scenario_name
        result["config_source"] = config_source
        result["threshold_config"] = threshold_config

        # Determine exit code based on pipeline outcome
        exit_code = EXIT_SUCCESS

        # Check TNR regression (exit code 2) — most severe non-error outcome
        verification = result.get("stages", {}).get("verification", {})
        if verification.get("result") == "regression":
            exit_code = EXIT_REGRESSION

        # Check safety requires human (exit code 3) — only if no regression
        if exit_code == EXIT_SUCCESS and safety_result is not None and safety_result.requires_human:
            exit_code = EXIT_APPROVAL_NEEDED

        # 5b. Incident Tracking (Story 6.3)
        tracker_issue_key = None
        tracker_url = None
        tracker_info = None
        if tracker is not None:
            tracker_issue_key = step_tracking(result, tracker)
            tracker_url = tracker.url
            if tracker_issue_key:
                result["stages"]["tracking"] = {
                    "issue_key": tracker_issue_key,
                    "url": tracker_url,
                }
                tracker_info = {"issue_key": tracker_issue_key, "url": tracker_url}
            else:
                tracker_info = {"issue_key": None, "url": None}

        # 5. Postmortem (still runs regardless of exit code)
        markdown_path, json_path = step_postmortem(
            result, incident,
            output_dir=args.output_dir,
            output_format=args.output_format,
            tracker_url=tracker_url,
            tracker_issue_key=tracker_issue_key,
        )

        # 5c. Attach postmortem to tracker (Story 6.3)
        if tracker is not None and tracker_issue_key:
            _attach_postmortem_to_tracker(tracker, tracker_issue_key, result)

        # 5d. Persiste incidente na memória institucional (Story 7.3)
        try:
            diagnosis = result.get("stages", {}).get("diagnosis", {})
            root_cause = diagnosis.get("root_cause") or ""
            if root_cause:
                detection = result.get("stages", {}).get("detection", {})
                affected_services = list(detection.get("affected_services", []))
                confidence = float(diagnosis.get("confidence") or 0.0)
                elapsed_total_local = time.time() - t_total
                tags = _extract_fault_tags(report)
                evidence = "; ".join(str(e) for e in diagnosis.get("evidence", [])[:5])
                record = IncidentRecord.new(
                    scenario=scenario_name,
                    root_cause=root_cause,
                    affected_services=affected_services,
                    diagnosis_confidence=confidence,
                    resolution_time_sec=elapsed_total_local,
                    tags=tags,
                    evidence_summary=evidence,
                )
                db = IncidentMemoryDB()
                db.save_incident(record)
        except Exception:
            pass

        # Summary
        print_summary(result, compression_stats, safety_result,
                      san_report=san_report, remediation_result=remediation_result,
                      tracker_info=tracker_info)

        elapsed_total = time.time() - t_total

        # Envia mensagem final ao Slack
        _diag = result.get("stages", {}).get("diagnosis", {})
        _conf_raw = _diag.get("confidence") or 0.0
        _CONF_MAP = {"high": 0.85, "medium": 0.5, "low": 0.25}
        if isinstance(_conf_raw, (int, float)):
            _conf = float(_conf_raw)
        else:
            _conf = _CONF_MAP.get(str(_conf_raw).lower(), 0.0)
        _mitigation_action = result.get("stages", {}).get("mitigation", {}).get("action")
        _fix = (
            _diag.get("proposed_fix")
            or _diag.get("fix_command")
            or (getattr(_mitigation_action, "command", None) if _mitigation_action else None)
            or ""
        )
        _br = (
            safety_result.blast_radius.scope
            if safety_result and safety_result.blast_radius
            else "unknown"
        )
        _slack_fire("pipeline.completed", cid=_pipeline_cid, data={
            "root_cause": _diag.get("root_cause", ""),
            "confidence": _conf,
            "proposed_fix": _fix,
            "risk_level": safety_result.risk_level.value if safety_result else "unknown",
            "blast_radius": _br,
            "evidence": list(_diag.get("evidence", []))[:5],
            "total_duration_ms": int(elapsed_total * 1000),
        })

        console.print(f"\n  [dim]Total execution time: {elapsed_total:.1f}s[/dim]")
        if markdown_path:
            console.print(f"  [dim]Postmortem (md): {markdown_path}[/dim]")
        if json_path:
            console.print(f"  [dim]Postmortem (json): {json_path}[/dim]")
        console.print(f"  [dim]Exit code: {exit_code} ({EXIT_CODE_LABELS[exit_code]})[/dim]")
        console.print()

        return exit_code

    except Exception as _exc:
        tb_text = traceback.format_exc()
        console.print(Text(_sanitize_secrets(tb_text), style="red"))
        _slack_fire("pipeline.failed", cid=_pipeline_cid, data={"error": _sanitize_secrets(str(_exc))})
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
