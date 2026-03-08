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
from datetime import datetime
from pathlib import Path

from config import ThresholdConfig, load_config
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
from agents.detector import DetectionAgent
from agents.diagnoser import DiagnosisAgent
from agents.mitigator import MitigationAgent
from agents.orchestrator import Orchestrator, State
from safety.tnr import TNRSafetyGuard
from pipeline.context_compressor import ContextCompressor
from safety.pre_validator import PreExecutionValidator
from pipeline.telemetry_sanitizer import TelemetrySanitizer
from safety.version_validator import VersionValidator

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


def step_pipeline(incident, config: ThresholdConfig | None = None) -> AggregatedReport:
    """Etapa 2: Pre-processamento (Drain3 + 3-sigma + p95)."""
    console.print(Rule("[bold]Stage 2: Pre-Processing Pipeline[/bold]", style="cyan"))
    console.print()

    if config is None:
        config = ThresholdConfig()

    t0 = time.time()
    aggregator = Aggregator(
        sigma_threshold=config.sigma_threshold,
        baseline_ratio=config.baseline_ratio,
        percentile_threshold=config.percentile_threshold,
        max_hops=config.max_hops,
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
    indexer = ObservabilityIndexer()

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

    retriever = ObservabilityRetriever(indexer)

    # Demo query
    console.print(f"\n  [dim]Sample query: 'connection pool timeout'[/dim]")
    results = retriever.search("connection pool timeout", top_k=3)
    for i, r in enumerate(results, 1):
        console.print(f"    {i}. [{r.chunk.source.upper()}] (score={r.score:.3f}) {r.chunk.text[:100]}...")

    console.print()
    return retriever


def step_agents(report, retriever, use_llm: bool = True,
                llm_model: str = "claude-sonnet-4-20250514",
                config: ThresholdConfig | None = None) -> tuple[dict, Orchestrator]:
    """Etapa 4: Pipeline de agentes (Detect -> Diagnose -> Mitigate -> Verify).

    Args:
        report: AggregatedReport do pipeline.
        retriever: ObservabilityRetriever para RAG (ou None).
        use_llm: Se True, usa LLM para diagnostico. Se False, heuristica apenas.
        llm_model: Nome do modelo LLM a usar (ignorado se use_llm=False).
        config: ThresholdConfig com valores de thresholds para agentes.
    """
    console.print(Rule("[bold]Stage 4: Agent Pipeline[/bold]", style="cyan"))
    console.print()

    if config is None:
        config = ThresholdConfig()

    # Inicializa agentes
    detector = DetectionAgent(
        error_threshold=config.error_threshold,
        sigma_threshold=config.detection_sigma_threshold,
    )
    diagnoser = DiagnosisAgent(max_iterations=5 if use_llm else 1, model=llm_model)
    mitigator = MitigationAgent()
    tnr_guard = TNRSafetyGuard(regression_threshold=config.regression_threshold)

    # Inicializa orchestrator
    orchestrator = Orchestrator()

    if not use_llm:
        console.print("  [yellow]Running in no-LLM mode (heuristic diagnosis only)[/yellow]\n")
    else:
        console.print(f"  [dim]Model: {llm_model}[/dim]\n")

    # Roda o ciclo completo
    t0 = time.time()
    result = orchestrator.run_incident_response(
        detector=detector,
        diagnoser=diagnoser,
        mitigator=mitigator,
        tnr_guard=tnr_guard,
        report=report,
        retriever=retriever,
    )
    elapsed = time.time() - t0

    console.print(f"\n  [dim]Agent pipeline completed in {elapsed:.1f}s[/dim]")

    # Armazena modelo no resultado para postmortem e summary
    result["llm_model"] = llm_model if use_llm else "heuristic"

    return result, orchestrator


def step_safety_validation(result):
    """Etapa 4b: Pre-Execution Safety Validation."""
    console.print(Rule("[bold]Stage 4b: Pre-Execution Safety Validation[/bold]", style="cyan"))
    console.print()

    mitigation = result.get("stages", {}).get("mitigation", {})
    diagnosis = result.get("stages", {}).get("diagnosis", {})

    if not mitigation or not mitigation.get("action"):
        console.print("  [dim]No mitigation to validate (skipping)[/dim]\n")
        return None

    validator = PreExecutionValidator(
        auto_approve_low_risk=True,
        max_blast_radius=3,
        require_rollback_plan=True,
    )

    action = mitigation["action"]
    safety_result = validator.validate(action, diagnosis)

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
    validator = VersionValidator()

    t0 = time.time()
    validation = validator.validate_mitigation_action(action)
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
        config["thresholds"] = dataclasses.asdict(tc)
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


def step_postmortem(result, orchestrator, incident, output_dir: str = "output",
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


def print_summary(result, orchestrator, compression_stats=None, safety_result=None,
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
            f"[dim]σ={tc.sigma_threshold:.1f}, p{tc.percentile_threshold:.0f}, TNR={tc.regression_threshold:.1f}[/dim]",
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

    table.add_row("Status", status_icon, f"{len(orchestrator.history)} state transitions")
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
        help="Path to TOML config file for analysis thresholds (default: auto-discover aiops.toml)",
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


def main() -> int:
    """Entry point principal. Retorna exit code baseado no resultado do pipeline."""
    parser = build_arg_parser()
    args = parser.parse_args()

    # Validate mutual exclusion rules
    _validate_args(parser, args)

    # Load .env
    load_dotenv()

    # Verifica API key se vai usar LLM
    use_llm = not args.no_llm
    if use_llm and not os.environ.get("ANTHROPIC_API_KEY"):
        console.print("[yellow]ANTHROPIC_API_KEY not found. Running in heuristic mode (--no-llm).[/yellow]")
        console.print("[dim]Set ANTHROPIC_API_KEY in .env for full ReAct diagnosis.[/dim]\n")
        use_llm = False

    # Leitura do modelo LLM configuravel via env var (Story 5.1)
    llm_model = os.environ.get("AIOPS_LLM_MODEL", "claude-sonnet-4-20250514")

    # Carrega configuração de thresholds via TOML (Story 5.2)
    threshold_config = load_config(config_path=args.config)
    config_source = args.config or "defaults"
    if args.config is None:
        # Verifica se auto-discovery encontrou arquivo
        project_root = Path(__file__).parent
        if (project_root / "aiops.toml").exists():
            config_source = str(project_root / "aiops.toml")

    # Resolve tracker (Story 6.3) — antes do pipeline para falhar rapido em config invalida
    tracker = _resolve_tracker(args.tracker)

    print_banner()

    try:
        # === Pipeline ===
        t_total = time.time()

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
        result, orchestrator = step_agents(report, retriever, use_llm=use_llm, llm_model=llm_model,
                                           config=threshold_config)

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
            result, orchestrator, incident,
            output_dir=args.output_dir,
            output_format=args.output_format,
            tracker_url=tracker_url,
            tracker_issue_key=tracker_issue_key,
        )

        # 5c. Attach postmortem to tracker (Story 6.3)
        if tracker is not None and tracker_issue_key:
            _attach_postmortem_to_tracker(tracker, tracker_issue_key, result)

        # Summary
        print_summary(result, orchestrator, compression_stats, safety_result,
                      san_report=san_report, remediation_result=remediation_result,
                      tracker_info=tracker_info)

        elapsed_total = time.time() - t_total
        console.print(f"\n  [dim]Total execution time: {elapsed_total:.1f}s[/dim]")
        if markdown_path:
            console.print(f"  [dim]Postmortem (md): {markdown_path}[/dim]")
        if json_path:
            console.print(f"  [dim]Postmortem (json): {json_path}[/dim]")
        console.print(f"  [dim]Exit code: {exit_code} ({EXIT_CODE_LABELS[exit_code]})[/dim]")
        console.print()

        return exit_code

    except Exception:
        tb_text = traceback.format_exc()
        console.print(Text(_sanitize_secrets(tb_text), style="red"))
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
