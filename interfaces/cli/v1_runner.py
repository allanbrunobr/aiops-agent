"""Composition root da CLI para o Blueprint Engine (V1).

Extraído de ``main.py`` (F5-2). Cada entrypoint mantém a própria composition
root (plano de refatoração, Fase 5); esta é a da CLI: monta engine, pipeline
e estado inicial via ``app.builders``, roda, exibe e salva.
``main.py`` expõe ``main_v1 = run_v1`` por compatibilidade.
"""

from __future__ import annotations

import argparse
import dataclasses
import time
from datetime import datetime

from rich.rule import Rule

from config.env import load_local_env
from data.registry import get_scenario
from data.sample_incident import create_incident
from domain.confidence import confidence_to_score
from domain.incident_state import IncidentState
from interfaces.cli.console import console
from interfaces.cli.display import display_results, save_postmortem_v1
from interfaces.cli.exit_codes import (
    EXIT_APPROVAL_NEEDED,
    EXIT_CODE_LABELS,
    EXIT_ERROR,
    EXIT_REGRESSION,
    EXIT_SUCCESS,
)


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

    from domain.alert import NormalizedAlert
    from domain.incident_state import IncidentState

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


def run_v1(args: argparse.Namespace) -> int:
    """Execucao via Blueprint Engine (V1).

    Composition root para modo CLI usando o Blueprint Engine.
    Substitui as chamadas step_*() sequenciais pelo pipeline tipado.
    """
    import asyncio
    import os

    from adapters.credentials import EnvCredentialProvider
    from adapters.persistence.in_memory_store import InMemoryStore
    from app.builders import build_engine, build_pipeline, build_rag
    from blueprint.engine import LLMCallLimitExceeded, PipelineAborted
    from config.loader import load_config as load_app_config
    from core.logging import configure_logging, get_logger

    load_local_env()

    log = get_logger("main")

    # 1. Configuracao e logging
    app_config = load_app_config(args.config)
    configure_logging()

    log.info(
        "main.started", no_llm=args.no_llm, no_rag=args.no_rag, compact=args.compact
    )

    # Slack notifier — opcional, wired se SLACK_BOT_TOKEN estiver no ambiente
    from datetime import timezone as _tz

    from core.lifecycle import LifecycleEvent
    from tools.slack_setup import setup_slack_notifier

    _slack_notifier = setup_slack_notifier(app_config)
    if _slack_notifier is not None:
        _slack_channel = os.environ.get("SLACK_CHANNEL", "").lstrip("#")
        log.info("slack.notifier_created", channel=_slack_channel)
        console.print(f"[cyan]Slack: notifier created for #{_slack_channel}[/cyan]")

    def _fire(
        event_type: str,
        node_name: str | None = None,
        data: dict | None = None,
        cid: str = "",
    ) -> None:
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
    engine = build_engine(app_config, store, creds)

    # 4. Constroi pipeline e estado inicial
    pipeline = build_pipeline(app_config)
    state = create_incident_state(args, incident_data)

    # 5. RAG (se habilitado)
    if not args.no_rag:
        try:
            console.print(Rule("[bold]Stage 3: RAG Indexing[/bold]", style="cyan"))
            # Indexa apos aggregator ter processado — por ora, sem report pre-pipeline
            indexer, retriever = build_rag(app_config)

            # Injeta retriever no metadata do estado
            state = dataclasses.replace(
                state,
                metadata={**state.metadata, "_retriever": retriever},
            )
        except Exception as exc:
            console.print(f"[yellow]RAG skipped: {exc}[/yellow]")

    # 6. Executa pipeline
    start_time = time.monotonic()
    exit_code = EXIT_SUCCESS

    _cid = state.correlation_id
    _fire(
        "pipeline.started",
        cid=_cid,
        data={
            "title": state.alert.title if state.alert else "AIOps Incident",
            "severity": state.alert.severity if state.alert else "unknown",
            "service": state.alert.service if state.alert else "unknown",
        },
    )

    try:
        console.print(
            Rule("[bold]Pipeline Execution (Blueprint Engine V1)[/bold]", style="cyan")
        )
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
        _fire(
            "pipeline.completed",
            cid=_cid,
            data={
                "root_cause": diagnosis.get("root_cause", ""),
                "confidence": confidence_to_score(_conf_v1),
                "proposed_fix": diagnosis.get("proposed_fix")
                or diagnosis.get("fix_command", ""),
                "risk_level": state.safety_verdict or "unknown",
                "evidence": diagnosis.get("evidence", []),
                "total_duration_ms": 0,  # preenchido abaixo
            },
        )

    elapsed = time.monotonic() - start_time

    # 7. Exibe resultados
    display_results(state, compact=args.compact)

    # 8. Salva postmortem
    output_dir = getattr(args, "output_dir", "output")
    if state.postmortem_md:
        save_postmortem_v1(state.postmortem_md, output_dir=output_dir)

    # 9. Resumo final
    console.print(Rule("[bold]Summary[/bold]", style="cyan"))
    console.print(f"Total time: {elapsed:.1f}s")
    console.print(
        f"Exit code: {exit_code} ({EXIT_CODE_LABELS.get(exit_code, 'unknown')})"
    )

    log.info("main.completed", exit_code=exit_code, duration_seconds=round(elapsed, 1))

    return exit_code


__all__ = ["create_incident_state", "run_v1"]
