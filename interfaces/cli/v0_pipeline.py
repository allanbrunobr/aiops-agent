"""Pipeline sequencial V0 da CLI (``step_*`` + ``run_v0``).

Movido de ``main.py`` em bloco e **sem reescrita** (F5-2): este engine será
removido inteiro em F5-4, quando o Blueprint Engine (V1) tiver paridade.
Refatorar Rich→Reporter aqui seria trabalho descartado — só as três
deduplicações exigidas por F5-2 tocam o corpo.

Os testes fazem ``patch("interfaces.cli.v0_pipeline.<nome>")``: tudo que
``run_v0`` chama precisa estar neste namespace (por isso os imports de
``print_banner``, ``step_postmortem``, agentes etc. são explícitos).
"""

from __future__ import annotations

import argparse
import os
import time
import traceback
import uuid
from datetime import datetime

import structlog as _structlog
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from adapters.reporter import RichConsoleReporter
from agents.detector import DetectionAgent
from agents.diagnoser import DiagnosisAgent
from agents.mitigator import MitigationAgent
from application.use_cases.postmortem import _no_incident
from config.loader import AppConfig, SafetyConfig, load_config, resolve_llm_model
from core.secrets import sanitize_secrets as _sanitize_secrets
from data.models import SimulatedIncident
from data.registry import get_scenario
from data.sample_incident import create_incident
from domain.confidence import confidence_to_score
from interfaces.cli.console import console
from interfaces.cli.data_sources import (
    _create_databricks_sources,
    step_fetch_databricks_data,
)
from interfaces.cli.display import _requires_human, print_banner, print_summary
from interfaces.cli.exit_codes import (
    EXIT_APPROVAL_NEEDED,
    EXIT_CODE_LABELS,
    EXIT_ERROR,
    EXIT_REGRESSION,
    EXIT_SUCCESS,
)
from interfaces.cli.postmortem import step_postmortem
from interfaces.cli.trackers import (
    _attach_postmortem_to_tracker,
    _resolve_tracker,
    step_tracking,
)
from pipeline.aggregator import AggregatedReport, Aggregator
from pipeline.context_compressor import ContextCompressor
from pipeline.telemetry_sanitizer import TelemetrySanitizer
from rag.indexer import ObservabilityIndexer
from rag.memory import IncidentMemoryDB, IncidentRecord
from rag.retriever_history import ObservabilityRetrieverWithHistory
from safety.orchestrator import SafetyOrchestrator
from safety.pre_validator import PreExecutionValidator
from safety.tnr import TNRSafetyGuard
from safety.version_validator import VersionValidator

_log = _structlog.get_logger("cli.v0")
_persist_log = _structlog.get_logger("main.persistence")


def step_generate_data(
    scenario_name: str | None = None,
) -> tuple[SimulatedIncident, str]:
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

    table = Table(
        title="Simulated Incident Data", border_style="cyan", show_header=True
    )
    table.add_column("Source", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Details")
    table.add_row("Scenario", "", f"[bold]{effective_scenario}[/bold]")
    table.add_row(
        "Logs", str(len(incident.logs)), f"{num_services} services, ERROR/WARN/INFO mix"
    )
    table.add_row(
        "Metrics", str(len(incident.metrics)), "Multi-service telemetry series"
    )
    table.add_row(
        "Traces", str(len(incident.traces)), "Distributed spans with bottleneck"
    )
    console.print(table)

    console.print(f"\n  [dim]Root cause: {incident.actual_root_cause}[/dim]")
    deploy = incident.deploy_time.strftime("%H:%M")
    spike = incident.spike_start.strftime("%H:%M")
    detect = incident.detection_time.strftime("%H:%M")
    fix = incident.fix_time.strftime("%H:%M")
    console.print(
        f"  [dim]Timeline: deploy@{deploy} -> spike@{spike} -> detect@{detect} -> fix@{fix}[/dim]\n"
    )

    return incident, effective_scenario


def step_sanitize(incident):
    """Etapa 1b: Sanitiza telemetria contra adversarial reward-hacking."""
    console.print(
        Rule(
            "[bold]Stage 1b: Telemetry Sanitization (AIOpsShield)[/bold]", style="cyan"
        )
    )
    console.print()

    t0 = time.time()
    sanitizer = TelemetrySanitizer(strict_mode=False)
    s_logs, s_metrics, s_traces, san_report = sanitizer.sanitize_all(
        incident.logs,
        incident.metrics,
        incident.traces,
    )
    elapsed = time.time() - t0

    # Display results
    table = Table(
        title="Telemetry Sanitization Results",
        border_style="red" if san_report.threat_detected else "green",
        show_header=True,
    )
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total entries analyzed", str(san_report.total_entries))
    table.add_row("Clean entries", f"[green]{san_report.clean_entries}[/green]")
    table.add_row(
        "Suspicious entries",
        f"[yellow]{san_report.suspicious_entries}[/yellow]"
        if san_report.suspicious_entries > 0
        else "0",
    )
    table.add_row(
        "Tainted entries",
        f"[red]{san_report.tainted_entries}[/red]"
        if san_report.tainted_entries > 0
        else "0",
    )
    table.add_row(
        "Adversarial entries",
        f"[bold red]{san_report.adversarial_entries}[/bold red]"
        if san_report.adversarial_entries > 0
        else "0",
    )
    table.add_row("Fields sanitized", str(san_report.fields_sanitized))
    console.print(table)

    if san_report.threat_detected:
        console.print("  [bold red]🛡️ ADVERSARIAL PAYLOADS BLOCKED:[/bold red]")
        for i, payload in enumerate(san_report.adversarial_payloads_blocked[:5], 1):
            console.print(f"    {i}. [dim]{payload[:100]}...[/dim]")
    else:
        console.print("  [green]✅ No adversarial payloads detected[/green]")

    console.print(f"  [dim]Sanitization time: {elapsed:.3f}s[/dim]")
    console.print(
        "  [dim]Defense: AIOpsShield (taint analysis + pattern matching + abstraction)[/dim]\n"
    )

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
    console.print(
        f"  [dim]Affected services: {', '.join(sorted(report.affected_services))}[/dim]\n"
    )

    return report


def step_compression(report):
    """Etapa 2b: Context Compression (Acon + AOI)."""
    console.print(
        Rule("[bold]Stage 2b: Context Compression (Acon + AOI)[/bold]", style="cyan")
    )
    console.print()

    compressor = ContextCompressor(compression_level="balanced")

    # Comprimir o report
    t0 = time.time()
    compressed_text, stats = compressor.compress(report)
    elapsed = time.time() - t0

    # Display stats
    table = Table(
        title="Context Compression Results", border_style="magenta", show_header=True
    )
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    # Original tokens uses report.to_text() length for comparison
    original_full_tokens = len(report.to_text()) // 4  # rough token estimate
    actual_ratio = 1 - (stats.compressed_tokens / max(original_full_tokens, 1))

    table.add_row("Full report tokens (est.)", f"{original_full_tokens:,}")
    table.add_row("Compressed tokens (est.)", f"{stats.compressed_tokens:,}")
    table.add_row(
        "Compression ratio",
        f"{max(actual_ratio, 0):.0%}"
        if actual_ratio > 0
        else "0% (input within budget)",
    )
    table.add_row("Chunks preserved", str(stats.chunks_kept))
    table.add_row("Chunks removed", str(stats.chunks_removed))
    table.add_row(
        "Critical info preserved", f"{min(stats.critical_preserved, 100):.0f}%"
    )
    console.print(table)

    console.print(f"  [dim]Compression time: {elapsed:.3f}s[/dim]")
    console.print(
        "  [dim]Level: balanced (priority-based classification + budget enforcement)[/dim]"
    )

    # Show priority breakdown
    by_priority = {}
    for name, count in [
        ("CRITICAL", 0),
        ("IMPORTANT", 0),
        ("CONTEXT", 0),
        ("NOISE", 0),
    ]:
        by_priority[name] = count
    # Count from the classified items in the compressor
    if hasattr(compressor, "_last_classified"):
        for item in compressor._last_classified:
            by_priority[item.priority.name] = by_priority.get(item.priority.name, 0) + 1

    console.print(
        f"  [dim]Compressed output: {len(compressed_text)} chars → ready for LLM[/dim]\n"
    )

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
        console.print(
            f"  Loaded [bold]{num_chunks}[/bold] chunks from cache in {elapsed:.2f}s"
        )
        console.print(f"  Cache: [dim]{cache_dir}[/dim]")
    else:
        if rebuild:
            console.print("  [dim]--rebuild-rag: forcing index reconstruction[/dim]")
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
            console.print(
                f"  [yellow]Warning: could not save index cache: {exc}[/yellow]"
            )
        elapsed = time.time() - t0
        console.print(f"  Indexed [bold]{num_chunks}[/bold] chunks in {elapsed:.2f}s")

    console.print("  Model: [dim]all-MiniLM-L6-v2 (384 dimensions)[/dim]")

    # F6-2: reranker por sinais de domínio (config.rag) — antes a CLI criava o
    # retriever sem reranker e o DomainReranker nunca rodava em produção.
    from app.builders import build_reranker

    retriever = ObservabilityRetrieverWithHistory(indexer, reranker=build_reranker())

    # Demo query
    console.print("\n  [dim]Sample query: 'connection pool timeout'[/dim]")
    results = retriever.search("connection pool timeout", top_k=3)
    for i, r in enumerate(results, 1):
        console.print(
            f"    {i}. [{r.chunk.source.upper()}] (score={r.score:.3f}) {r.chunk.text[:100]}..."
        )

    # Exibe precedentes históricos antes do diagnóstico
    try:
        services = list(report.affected_services)
        tags = _extract_fault_tags(report)
        precedents = retriever.get_similar_past_incidents(services, tags, limit=3)
        if precedents:
            console.print("\n  [bold cyan]Similar past incidents found:[/bold cyan]")
            for p in precedents:
                console.print(f"    • {p['scenario']}: {p['root_cause']}")
    except Exception as exc:  # best-effort: precedentes não bloqueiam o pipeline
        _log.warning("cli.v0.precedents_failed", error=str(exc))

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


def step_agents(
    report,
    retriever,
    use_llm: bool = True,
    llm_model: str = "claude-sonnet-4-20250514",
    config: AppConfig | None = None,
) -> dict:
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

    # Reporter compartilhado: preserva o output Rich da CLI.
    _reporter = RichConsoleReporter(console=console)

    # Inicializa agentes (Estágio 1 / fallback --no-llm)
    detector = DetectionAgent(
        error_threshold=config.thresholds.error_threshold,
        sigma_threshold=config.thresholds.detection_sigma_threshold,
        reporter=_reporter,
    )

    if use_llm:
        # Estágio 2: agente autônomo com plan/execute/reflect
        from adapters.llm.anthropic_client import create_anthropic_llm_client
        from agents.orchestrator import AgentOrchestrator

        _client = create_anthropic_llm_client()
        _orchestrator = AgentOrchestrator(
            client=_client,
            model=llm_model,
            memory_db=_memory_db,
            skill_router=_skill_router,
            config=config,
            reporter=_reporter,
        )
    # Estágio 1 (fallback --no-llm): DiagnosisAgent heurístico
    diagnoser = DiagnosisAgent(
        max_iterations=1,
        model=llm_model,
        similar_incidents=_similar_incidents,
        skill=_skill,
        reporter=_reporter,
    )
    mitigator = MitigationAgent(reporter=_reporter)
    tnr_guard = TNRSafetyGuard(
        regression_threshold=config.thresholds.regression_threshold
    )

    if not use_llm:
        console.print(
            "  [yellow]Running in no-LLM mode (heuristic diagnosis only)[/yellow]\n"
        )
    else:
        console.print(f"  [dim]Model: {llm_model} — Stage 2 AgentOrchestrator[/dim]\n")

    # Executa pipeline de agentes (logica migrada de agents.orchestrator)
    t0 = time.time()
    result: dict = {"success": False, "stages": {}}
    timeline: list[dict] = []

    def _transition(from_state: str, to_state: str, reason: str) -> None:
        timeline.append(
            {
                "time": datetime.now().strftime("%H:%M:%S"),
                "from": from_state,
                "to": to_state,
                "reason": reason,
            }
        )
        console.print(f"\n  [{to_state}] {reason}", style="bold")

    # 1. DETECTING
    _transition("IDLE", "DETECTING", "Analisando dados de observabilidade...")
    detection = detector.detect(report)
    result["stages"]["detection"] = detection

    if not detection.get("is_incident"):
        _transition("DETECTING", "IDLE", "Nenhum incidente detectado")
        result["timeline"] = timeline
        result["llm_model"] = llm_model if use_llm else "heuristic"
        console.print(
            f"\n  [dim]Agent pipeline completed in {time.time() - t0:.1f}s[/dim]"
        )
        return result

    # 2. DIAGNOSING
    affected = ", ".join(detection.get("affected_services", []))
    _transition("DETECTING", "DIAGNOSING", f"Investigando incidente em {affected}")
    if use_llm:
        # Detecção já feita acima com config.thresholds — não detectar de novo
        _outcome = _orchestrator.run(report, retriever, detection=detection)
        diagnosis = _outcome.diagnosis
        # Propaga atributos usados no REFLECT+WRITE do Estágio 1
        diagnoser.tools_used = _outcome.tools_used
        diagnoser.iteration_count = _outcome.iterations
    else:
        diagnosis = diagnoser.heuristic_diagnosis(report)
        diagnoser.tools_used = []
        diagnoser.iteration_count = 0
    result["stages"]["diagnosis"] = diagnosis

    if not diagnosis.get("root_cause"):
        _transition("DIAGNOSING", "FAILED", "Não foi possível identificar root cause")
        result["timeline"] = timeline
        result["llm_model"] = llm_model if use_llm else "heuristic"
        console.print(
            f"\n  [dim]Agent pipeline completed in {time.time() - t0:.1f}s[/dim]"
        )
        return result

    # 3. MITIGATING
    _transition("DIAGNOSING", "MITIGATING", "Propondo remediação...")
    if use_llm:
        # Reutiliza mitigation já computada dentro do orchestrator (evita duplicata)
        mitigation = _outcome.mitigation
    else:
        # propose_safe degrada uma falha do mitigator para revisão manual
        # (command="", risk HIGH) em vez de derrubar o pipeline (NOVO-01).
        mitigation = mitigator.propose_safe(diagnosis)
    result["stages"]["mitigation"] = mitigation

    # 4. VERIFYING (TNR) — measured-only (D5): sem medição pós-ação real o
    # resultado é NOT_VERIFIED, não um delta inventado (F09).
    _transition("MITIGATING", "VERIFYING", "Verificando impacto (TNR)...")
    if use_llm:
        # Reutiliza resultado TNR já computado dentro do orchestrator
        _tnr_value = _outcome.tnr_result or (
            "success" if _outcome.tnr_passed else "not_verified"
        )
        _tnr_regression = _tnr_value == "regression"
        _tnr_msg = _outcome.tnr_message
        result["stages"]["verification"] = {
            "result": _tnr_value,
            "health_delta": _outcome.tnr_health_delta,
            "message": _tnr_msg,
            "verification_mode": _outcome.tnr_verification_mode,
        }
    else:
        tnr_result = mitigator.verify_with_tnr(mitigation, tnr_guard)
        _tnr_value = tnr_result.result.value
        _tnr_regression = _tnr_value == "regression"
        _tnr_msg = tnr_result.message
        result["stages"]["verification"] = {
            "result": _tnr_value,
            "health_delta": tnr_result.health_delta,
            "message": _tnr_msg,
            "verification_mode": tnr_result.verification_mode,
        }

    if _tnr_regression:
        _transition("VERIFYING", "ROLLBACK", f"Regressão detectada: {_tnr_msg}")
        result["stages"]["rollback"] = {
            "message": "Rollback executado, retornando ao diagnostico"
        }
    else:
        _label = (
            "TNR check passed; mitigation proposed, safety gate decides next"
            if _tnr_value == "success"
            else "Mitigation proposed (not executed); safety gate decides next"
        )
        _transition("VERIFYING", "RESOLVED", f"{_label}: {_tnr_msg}")
        result["success"] = True

    # ── Skill Evolution: REFLECT + WRITE ─────────────────────────────────
    # Reflexão pós-TNR e persistência de outcome no SQLite.
    _tnr_str = result.get("stages", {}).get("verification", {}).get("result", "unknown")
    _tnr_passed = _tnr_str == "success"

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
        import datetime as _dt

        from rag.memory import SkillOutcome

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
    except Exception as exc:  # write/evolve é best-effort; não bloqueia o pipeline
        _log.warning("cli.v0.skill_memory_write_failed", error=str(exc))
    # ─────────────────────────────────────────────────────────────────────

    result["timeline"] = timeline
    result["llm_model"] = llm_model if use_llm else "heuristic"
    console.print(f"\n  [dim]Agent pipeline completed in {time.time() - t0:.1f}s[/dim]")
    return result


def _record_safety_decision(result, safety_result, remediation_result) -> bool:
    """Grava em result['stages']['safety_decision'] o veredito combinado.

    O postmortem (markdown E JSON) e o exit code consultam este veredito:
    uma camada que aprova não anula a que bloqueia (F03).
    """
    blocked_by = []
    if safety_result is not None and safety_result.requires_human:
        blocked_by.append("pre_validator")
    if remediation_result is not None and not remediation_result.is_safe:
        blocked_by.append("version_validator")
    requires_human = _requires_human(safety_result, remediation_result)
    result.setdefault("stages", {})["safety_decision"] = {
        "requires_human": requires_human,
        "blocked_by": blocked_by,
    }
    return requires_human


def step_skill_learning(memory_db, skill, *, tnr_result: str, reflection: dict,
                        tools_used: list, iteration_count: int, use_llm: bool, client):
    """Fases WRITE + EVOLVE do ciclo de skills: grava o outcome e, se a skill
    estiver fraca, propõe uma reescrita (pendente de revisão humana).

    Extraída de main.py (F5-2) e re-exportada por main para os consumidores do
    CLI e para os testes de aprendizado de skills.

    Returns:
        A skill candidata proposta, ou None.
    """
    import datetime as _dt

    from rag.memory import SkillOutcome

    skill_id = getattr(skill, "skill_id", "default")
    tnr_passed = tnr_result == "success"
    outcome = SkillOutcome(
        outcome_id=str(uuid.uuid4()),
        skill_id=skill_id,
        incident_id=str(uuid.uuid4()),
        tnr_passed=tnr_passed,
        reflection_score=float(reflection.get("score", 0.5)),
        reflection_lesson=str(reflection.get("lesson", "")),
        tools_used=tools_used,
        iteration_count=iteration_count,
        timestamp=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    memory_db.save_skill_outcome(outcome)

    # Atualiza utility_score/total_uses (EMA). Sem LLM a "reflexão" é um
    # placeholder (0.5) — não há sinal para aprender (F16).
    if use_llm and skill is not None:
        memory_db.update_skill_score(skill_id, tnr_passed, outcome.reflection_score)
        skill = memory_db.get_skill(skill_id) or skill

    # ── Fase EVOLVE — reescreve skills fracas (pendente de revisão) ──
    from agents.skill_evolver import SkillEvolver

    if skill is None:
        return None
    evolver = SkillEvolver(client=client, memory_db=memory_db)
    if not evolver.should_evolve(skill):
        return None
    evolved = evolver.evolve(skill)
    if evolved:
        console.print(
            f"  [dim]Skill rewrite proposed for {evolved.name} "
            f"(v{evolved.version}) — pending human review[/dim]"
        )
    return evolved


def step_safety_validation(result, config: AppConfig | None = None):
    """Etapa 4b: Pre-Execution Safety Validation.

    Usa ``safety.require_human_approval`` e ``safety.blast_radius_limit`` da
    config (antes fixava auto-aprovação e limite 3, ignorando o YAML — F03/F13).
    """
    console.print(
        Rule("[bold]Stage 4b: Pre-Execution Safety Validation[/bold]", style="cyan")
    )
    console.print()

    mitigation = result.get("stages", {}).get("mitigation", {})
    diagnosis = result.get("stages", {}).get("diagnosis", {})

    if not mitigation or not mitigation.get("action"):
        console.print("  [dim]No mitigation to validate (skipping)[/dim]\n")
        return None

    safety_cfg = getattr(config, "safety", None) or SafetyConfig()
    pre_validator = PreExecutionValidator(
        auto_approve_low_risk=not safety_cfg.require_human_approval,
        max_blast_radius=safety_cfg.blast_radius_limit,
        require_rollback_plan=True,
    )
    orchestrator = SafetyOrchestrator(pre_validator=pre_validator)

    action = mitigation["action"]
    orch_result = orchestrator.validate(action, diagnosis)
    safety_result = orch_result.layer_details["pre_validator"]

    # Display results
    table = Table(
        title="Pre-Execution Safety Check",
        border_style="red" if not safety_result.is_safe else "green",
        show_header=True,
    )
    table.add_column("Check", style="bold")
    table.add_column("Result")

    # Risk level with color
    risk_colors = {
        "LOW": "green",
        "MEDIUM": "yellow",
        "HIGH": "red",
        "CRITICAL": "bold red",
    }
    risk_color = risk_colors.get(safety_result.risk_level.value, "white")
    table.add_row(
        "Risk Level", f"[{risk_color}]{safety_result.risk_level.value}[/{risk_color}]"
    )
    table.add_row("Risk Score", f"{safety_result.risk_score:.0f}/100")
    table.add_row("Approval", safety_result.approval_status.value)
    table.add_row(
        "Requires Human", "Yes ⚠️" if safety_result.requires_human else "No ✅"
    )

    # Blast radius
    br = safety_result.blast_radius
    table.add_row(
        "Blast Radius",
        f"{br.scope} ({br.affected_services} services, ~{br.affected_pods_estimate} pods)",
    )
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
    console.print(
        Rule("[bold]Stage 4c: Remediation Version/CVE Validation[/bold]", style="cyan")
    )
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
    table = Table(
        title="Remediation CVE/Version Check",
        border_style=border_color,
        show_header=True,
    )
    table.add_column("Check", style="bold")
    table.add_column("Result")

    risk_colors = {
        "SAFE": "green",
        "CAUTION": "yellow",
        "DANGEROUS": "red",
        "MALICIOUS": "bold red",
    }
    risk_color = risk_colors.get(validation.risk.name, "white")
    table.add_row("Risk Level", f"[{risk_color}]{validation.risk.name}[/{risk_color}]")
    table.add_row("Is Safe", "✅ Yes" if validation.is_safe else "❌ No")

    if validation.checks:
        for check in validation.checks:
            status = "✅" if check.is_safe else "❌"
            cve_str = (
                f" ({', '.join(check.known_cves[:2])})" if check.known_cves else ""
            )
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
def step_predict(
    result: dict, report, *, slack_fire=None, correlation_id: str = ""
) -> None:
    """Etapa 4d: Estágio preditivo — gera PredictionReport e emite eventos de lifecycle.

    Executa análise preditiva sobre o aggregated_report e armazena o resultado
    em result["stages"]["prediction"]. Emite evento de lifecycle se event_level
    for definido. Nunca lança exceção — falhas retornam prediction=None.

    Args:
        result: Dict com estágios do pipeline — recebe prediction em stages.prediction.
        report: AggregatedReport com dados pré-processados.
        slack_fire: Callback opcional para envio de evento ao Slack.
        correlation_id: ID de correlação do incidente para rastreio no Slack.
    """
    console.print(Rule("[bold]Stage 4d: Predictive Analysis[/bold]", style="cyan"))
    console.print()

    try:
        import uuid
        from datetime import datetime, timezone

        from core.lifecycle import LifecycleEvent
        from pipeline.predictive_stage import run_predictive_stage

        prediction = run_predictive_stage(report)
        result["stages"]["prediction"] = prediction

        if prediction is not None:
            console.print(f"  Risk score: [bold]{prediction.risk_score:.1f}/100[/bold]")
            console.print(f"  Signals: {', '.join(prediction.signals) or 'none'}")
            if prediction.event_level:
                color = (
                    "red" if prediction.event_level == "prediction.alert" else "yellow"
                )
                console.print(f"  Event: [{color}]{prediction.event_level}[/{color}]")

                # Emite evento de lifecycle
                event = LifecycleEvent(
                    event_type=prediction.event_level,
                    node_name="predictive_stage",
                    correlation_id=correlation_id or str(uuid.uuid4()),
                    timestamp=datetime.now(timezone.utc),
                    data={
                        "event_level": prediction.event_level,
                        "risk_score": prediction.risk_score,
                        "eta_minutes": prediction.eta_minutes,
                        "signals": prediction.signals,
                        "matched_history": getattr(prediction, "matched_history", []),
                    },
                )
                console.print(
                    f"  [dim]Lifecycle event emitted: {event.event_type}[/dim]"
                )

                # Envia para o Slack se callback disponível
                if slack_fire is not None:
                    slack_fire(
                        prediction.event_level,
                        cid=event.correlation_id,
                        data=event.data,
                    )
            else:
                console.print("  [dim]No lifecycle event (risk below threshold)[/dim]")
        else:
            console.print(
                "  [yellow]Predictive stage returned None (graceful degradation)[/yellow]"
            )

    except Exception as exc:
        console.print(f"  [yellow]Predictive stage error (non-fatal): {exc}[/yellow]")
        result["stages"]["prediction"] = None

    console.print()


def step_persist_incident(
    result,
    report,
    scenario_name: str,
    elapsed_sec: float,
    db: IncidentMemoryDB | None = None,
) -> bool:
    """Etapa 5d: persiste o incidente na memória institucional (Story 7.3).

    Best-effort: falha não derruba o pipeline, mas é registrada em log. Usa
    ``confidence_to_score`` (aceita confiança textual "medium"/"high" — antes
    ``float("medium")`` levantava ValueError e todo save falhava, F07).

    Returns:
        True se gravou; False se não havia root_cause ou se a gravação falhou.
    """
    diagnosis = result.get("stages", {}).get("diagnosis", {})
    root_cause = diagnosis.get("root_cause") or ""
    if not root_cause:
        return False
    try:
        detection = result.get("stages", {}).get("detection", {})
        record = IncidentRecord.new(
            scenario=scenario_name,
            root_cause=root_cause,
            affected_services=list(detection.get("affected_services", [])),
            diagnosis_confidence=confidence_to_score(diagnosis.get("confidence")),
            resolution_time_sec=elapsed_sec,
            tags=_extract_fault_tags(report),
            evidence_summary="; ".join(
                str(e) for e in diagnosis.get("evidence", [])[:5]
            ),
        )
        (db if db is not None else IncidentMemoryDB()).save_incident(record)
        return True
    except Exception as exc:  # noqa: BLE001 — best-effort, mas registrado
        _persist_log.warning(
            "persistence.save_incident_failed",
            scenario=scenario_name,
            error=repr(exc),
        )
        return False


def run_v0(args: argparse.Namespace) -> int:
    """Composition root do engine V0: executa os ``step_*`` em sequência.

    Ex-corpo de ``main()``. ``main()`` já normalizou ``args.no_llm`` (sem
    ANTHROPIC_API_KEY força heurística) antes de delegar para aqui.
    """
    use_llm = not args.no_llm

    print_banner()

    # Slack notifier — opcional, sem impacto no pipeline se falhar
    import asyncio as _asyncio
    from datetime import timezone as _tz2

    from app.slack_setup import setup_slack_notifier as _setup_slack
    from core.lifecycle import LifecycleEvent as _LifecycleEvent

    _notifier = _setup_slack()
    if _notifier is not None:
        _slack_ch = os.environ.get("SLACK_CHANNEL", "").lstrip("#")
        console.print(f"[cyan]Slack: notificacoes ativas em #{_slack_ch}[/cyan]")

    def _slack_fire(
        event_type: str, data: dict | None = None, cid: str = "unknown"
    ) -> None:
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
            console.print(
                f"[yellow]Slack: falha ao enviar {event_type}: {_fe}[/yellow]"
            )

    # Carrega configuracao via YAML (Pydantic Settings)
    threshold_config = load_config(config_path=args.config)
    config_source = args.config or "config/defaults.yaml"

    # Modelo LLM: AIOPS_LLM_MODEL (legado) > llm.model_name do YAML > padrão
    # (A4/ARQ-08/F5-09 — antes só a env var era lida e o YAML não tinha efeito)
    llm_model = resolve_llm_model(threshold_config)

    # Resolve tracker (Story 6.3) — antes do pipeline para falhar rapido em config invalida
    tracker = _resolve_tracker(args.tracker)

    import uuid as _uuid

    _pipeline_cid = f"inc-{datetime.now().strftime('%Y%m%d')}-{_uuid.uuid4().hex[:8]}"

    try:
        # === Pipeline ===
        t_total = time.time()

        _slack_fire(
            "pipeline.started",
            cid=_pipeline_cid,
            data={
                "title": f"AIOps: {args.scenario or 'payment-pool-exhaustion'}",
                "severity": "critical",
                "service": "payment-svc",
            },
        )

        # 1. Data Generation / Fetch
        if args.data_source == "databricks":
            sources = _create_databricks_sources()
            if sources is None:
                console.print(
                    "[red]Cannot proceed without Databricks connection. Exiting.[/red]"
                )
                return EXIT_ERROR
            log_source, metrics_source, trace_source = sources
            incident, scenario_name = step_fetch_databricks_data(
                log_source,
                metrics_source,
                trace_source,
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
                console.print(
                    "[yellow]Warning: --rebuild-rag ignored because --no-rag is set[/yellow]"
                )
            console.print(
                Rule(
                    "[bold]Stage 3: RAG Indexing[/bold] [dim](skipped)[/dim]",
                    style="cyan",
                )
            )
            console.print()
            retriever = None

        # 4. Agent Pipeline
        result = step_agents(
            report,
            retriever,
            use_llm=use_llm,
            llm_model=llm_model,
            config=threshold_config,
        )

        # 4d. Predictive Analysis
        step_predict(
            result, report, slack_fire=_slack_fire, correlation_id=_pipeline_cid
        )

        # 4b. Pre-Execution Safety Validation
        safety_result = step_safety_validation(result, config=threshold_config)

        # 4c. Remediation Version/CVE Validation
        remediation_result = step_remediation_validation(result)

        # 4d. Veredito combinado: qualquer camada que bloqueia exige humano.
        requires_human = _record_safety_decision(
            result, safety_result, remediation_result
        )

        # 4e. Notificação Slack: diagnóstico + correção proposta
        _diag_for_slack = result.get("stages", {}).get("diagnosis", {})
        _mitigation_for_slack = result.get("stages", {}).get("mitigation", {})
        _fix_action = (
            _mitigation_for_slack.get("action") if _mitigation_for_slack else None
        )
        _proposed_fix_slack = (
            _diag_for_slack.get("proposed_fix")
            or _diag_for_slack.get("fix_command")
            or (getattr(_fix_action, "command", None) if _fix_action else None)
            or ""
        )
        _conf_for_slack = confidence_to_score(_diag_for_slack.get("confidence"))
        # Sem incidente não há diagnóstico nem correção a anunciar: o Slack
        # postava "Diagnóstico + Correção Proposta" com Root Cause em branco.
        if not _no_incident(result):
            _slack_fire(
                "diagnosis.completed",
                cid=_pipeline_cid,
                data={
                    "root_cause": _diag_for_slack.get("root_cause") or "",
                    "proposed_fix": _proposed_fix_slack,
                    "risk_level": safety_result.risk_level.value
                    if safety_result
                    else "unknown",
                    "requires_human": requires_human,
                    "confidence": float(_conf_for_slack),
                    "evidence": list(_diag_for_slack.get("evidence", []))[:5],
                },
            )

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
        if exit_code == EXIT_SUCCESS and requires_human:
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
            result,
            incident,
            output_dir=args.output_dir,
            output_format=args.output_format,
            tracker_url=tracker_url,
            tracker_issue_key=tracker_issue_key,
        )

        # 5c. Attach postmortem to tracker (Story 6.3)
        if tracker is not None and tracker_issue_key:
            _attach_postmortem_to_tracker(tracker, tracker_issue_key, result)

        # 5d. Persiste incidente na memória institucional (Story 7.3)
        step_persist_incident(
            result, report, scenario_name, time.time() - t_total
        )

        # Summary
        print_summary(
            result,
            compression_stats,
            safety_result,
            san_report=san_report,
            remediation_result=remediation_result,
            tracker_info=tracker_info,
        )

        elapsed_total = time.time() - t_total

        # Envia mensagem final ao Slack
        _diag = result.get("stages", {}).get("diagnosis", {})
        _conf = confidence_to_score(_diag.get("confidence"))
        _mitigation_action = (
            result.get("stages", {}).get("mitigation", {}).get("action")
        )
        _fix = (
            _diag.get("proposed_fix")
            or _diag.get("fix_command")
            or (
                getattr(_mitigation_action, "command", None)
                if _mitigation_action
                else None
            )
            or ""
        )
        _br = (
            safety_result.blast_radius.scope
            if safety_result and safety_result.blast_radius
            else "unknown"
        )
        _slack_fire(
            "pipeline.completed",
            cid=_pipeline_cid,
            data={
                "root_cause": _diag.get("root_cause") or "",
                "confidence": _conf,
                "proposed_fix": _fix,
                "risk_level": safety_result.risk_level.value
                if safety_result
                else "unknown",
                "blast_radius": _br,
                "evidence": list(_diag.get("evidence", []))[:5],
                "total_duration_ms": int(elapsed_total * 1000),
                "no_incident": _no_incident(result),
            },
        )

        console.print(f"\n  [dim]Total execution time: {elapsed_total:.1f}s[/dim]")
        if markdown_path:
            console.print(f"  [dim]Postmortem (md): {markdown_path}[/dim]")
        if json_path:
            console.print(f"  [dim]Postmortem (json): {json_path}[/dim]")
        console.print(
            f"  [dim]Exit code: {exit_code} ({EXIT_CODE_LABELS[exit_code]})[/dim]"
        )
        console.print()

        return exit_code

    except Exception as _exc:
        tb_text = traceback.format_exc()
        console.print(Text(_sanitize_secrets(tb_text), style="red"))
        _slack_fire(
            "pipeline.failed",
            cid=_pipeline_cid,
            data={"error": _sanitize_secrets(str(_exc))},
        )
        return EXIT_ERROR


__all__ = [
    "run_v0",
    "step_generate_data",
    "step_sanitize",
    "step_pipeline",
    "step_compression",
    "step_rag",
    "step_agents",
    "step_safety_validation",
    "step_remediation_validation",
    "_record_safety_decision",
    "step_predict",
    "_extract_fault_tags",
]
