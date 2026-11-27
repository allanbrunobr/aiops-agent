"""Composition root da CLI para o Blueprint Engine (V1).

Extraído de ``main.py`` (F5-2). Cada entrypoint mantém a própria composition
root (plano de refatoração, Fase 5); esta é a da CLI: monta engine, pipeline
e estado inicial via ``app.builders``, roda, exibe e salva.
``main.py`` expõe ``main_v1 = run_v1`` por compatibilidade.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from rich.rule import Rule

from config.env import load_local_env
from data.registry import get_scenario
from data.sample_incident import create_incident
from domain.confidence import confidence_to_score
from domain.incident_state import IncidentState
from interfaces.cli.console import console
from interfaces.cli.data_sources import (
    _create_databricks_sources,
    drop_invalid_metric_points,
    step_fetch_databricks_data,
)
from interfaces.cli.display import display_results, print_summary, save_postmortem_v1
from interfaces.cli.exit_codes import (
    EXIT_APPROVAL_NEEDED,
    EXIT_CODE_LABELS,
    EXIT_ERROR,
    EXIT_REGRESSION,
    EXIT_SUCCESS,
)
from interfaces.cli.trackers import (
    _attach_postmortem_to_tracker,
    _resolve_tracker,
    step_tracking,
)

RAG_CACHE_DIR = ".aiops-cache/rag"

_persist_log = structlog.get_logger("main.persistence")


def _state_after_abort(exc, config):
    """Estado a exibir depois de um PipelineAborted no V1.

    A engine anexa ao aborto o estado alcançado antes do node que falhou. Num
    bloqueio de safety esse estado já tem o diagnóstico: marca o veredito como
    BLOCKED e gera o postmortem (template, sem LLM) com a seção de safety —
    o mesmo que o V0 mostra quando uma camada exige humano.
    """
    from blueprint.adapters.safety_nodes import SafetyBlockedError

    state = getattr(exc, "state", None)
    if state is None or not isinstance(exc.__cause__, SafetyBlockedError):
        return state

    node_id = getattr(exc, "node_id", None) or "safety"
    reason = f"{node_id}: {exc.__cause__}"
    state = dataclasses.replace(
        state,
        safety_verdict="BLOCKED",
        safety_reasons=[*state.safety_reasons, reason],
    )
    if state.diagnosis and not state.postmortem_md:
        try:
            from blueprint.adapters.pipeline_nodes import make_postmortem_node

            state = make_postmortem_node(config)(state)
        except Exception as pm_exc:  # noqa: BLE001 — sem postmortem, mas registrado
            _persist_log.warning("v1.postmortem_after_block_failed", error=repr(pm_exc))
    if state.postmortem_md:
        state = dataclasses.replace(
            state,
            postmortem_md=(
                state.postmortem_md
                + "\n## Safety\n"
                + f"- **Verdict:** BLOCKED by `{node_id}`\n"
                + f"- **Reason:** {exc.__cause__}\n"
                + "- The proposed action was not approved and requires human review.\n"
            ),
        )
    return state


def _dominant_service(incident: object | None) -> str:
    """Serviço mais frequente nos logs do incidente (``unknown`` sem logs)."""
    from collections import Counter

    counts = Counter(
        getattr(entry, "service", None)
        for entry in (getattr(incident, "logs", None) or [])
        if getattr(entry, "service", None)
    )
    return counts.most_common(1)[0][0] if counts else "unknown"


def create_incident_state(
    args: argparse.Namespace,
    incident_data: dict,
    incident: object | None = None,
    scenario_name: str | None = None,
) -> "IncidentState":
    """Cria IncidentState a partir de dados simulados e flags da CLI.

    Args:
        args: Argumentos da CLI.
        incident_data: Dicionario com logs, metrics, traces do simulador.
        incident: SimulatedIncident de origem; guardado em ``metadata["_incident"]``
            (o postmortem rico lê deploy/spike/detection/fix times dele).
        scenario_name: Rótulo da fonte (ex.: ``databricks-live``). Para dado real
            o alerta não herda o texto do cenário simulado de payment-pool.

    Returns:
        IncidentState pronto para o pipeline.
    """
    import uuid
    from datetime import timezone

    from domain.alert import NormalizedAlert
    from domain.incident_state import IncidentState

    now = datetime.now(timezone.utc)
    correlation_id = f"inc-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"

    live = scenario_name is not None and scenario_name.endswith("-live")
    if live:
        alert_fields = dict(
            source=scenario_name,
            source_id="live-001",
            title=f"Live telemetry window ({scenario_name})",
            description=f"Telemetry fetched from {scenario_name}; no simulated scenario",
            service=_dominant_service(incident),
        )
    else:
        alert_fields = dict(
            source="simulated",
            source_id="sim-001",
            title="Simulated incident from data/sample_incident.py",
            description="Payment service connection pool exhaustion after config deploy",
            service="payment-svc",
        )
    alert = NormalizedAlert(
        correlation_id=correlation_id,
        severity="critical",
        alert_type="HighErrorRate",
        timestamp=now,
        detection_timestamp=now,
        environment="production",
        **alert_fields,
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
            **({"_incident": incident} if incident is not None else {}),
            "scenario": scenario_name
            or getattr(args, "scenario", None)
            or "payment-pool-exhaustion",
            "output_format": getattr(args, "output_format", "markdown"),
            "compact": getattr(args, "compact", False),
        },
    )


def _save_outputs(
    state: IncidentState,
    args: argparse.Namespace,
    tracker: object | None,
    *,
    aborted: bool,
) -> dict[str, Any] | None:
    """Tracking + postmortem nos formatos de ``--output-format`` (como o V0).

    Retorna o ``tracker_info`` para a tabela-resumo (None sem tracker).
    """
    from application.use_cases.postmortem import (
        json_default,
        render_postmortem_markdown,
        serialize_result,
    )
    from blueprint.adapters.state_adapter import state_to_result

    output_dir = getattr(args, "output_dir", "output")
    fmt = getattr(args, "output_format", "markdown")
    incident = state.metadata.get("_incident")
    markdown = state.postmortem_md
    result = state_to_result(state) if state.diagnosis else None

    issue_key = None
    if tracker is not None and result is not None:
        issue_key = step_tracking(result, tracker)
        if issue_key:
            result["stages"]["tracking"] = {"issue_key": issue_key, "url": tracker.url}
            if markdown and not aborted and incident is not None:
                # O node de postmortem rodou antes do tracking: re-renderiza
                # com a seção Incident Tracking.
                markdown = render_postmortem_markdown(
                    result,
                    incident,
                    now=datetime.now(),
                    tracker_url=tracker.url,
                    tracker_issue_key=issue_key,
                )

    if markdown and fmt in ("markdown", "both"):
        save_postmortem_v1(markdown, output_dir=output_dir)

    if fmt in ("json", "both"):
        if result is None or incident is None:
            console.print("[yellow]JSON postmortem skipped: no diagnosis.[/yellow]")
        else:
            out = Path(output_dir)
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"postmortem_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
            path.write_text(
                json.dumps(
                    serialize_result(result, incident),
                    default=json_default,
                    indent=2,
                    ensure_ascii=False,
                )
            )
            console.print(f"[green]Postmortem (JSON) saved: {path}[/green]")

    if tracker is not None and issue_key and result is not None:
        _attach_postmortem_to_tracker(tracker, issue_key, result)

    if tracker is None:
        return None
    return {"issue_key": issue_key, "url": getattr(tracker, "url", "")}


def _persist_memory(state, args, elapsed: float) -> None:
    """Grava incidente + outcome de skill; falha nunca muda o exit code."""
    from blueprint.adapters.pipeline_nodes import record_run_memory

    use_llm = not getattr(args, "no_llm", False)
    client = None
    if use_llm:
        try:
            import os

            from adapters.llm.anthropic_client import create_anthropic_llm_client

            key = os.environ.get("ANTHROPIC_API_KEY", "")
            client = create_anthropic_llm_client(api_key=key) if key else None
        except Exception as exc:  # noqa: BLE001 — evolver degrada sem client
            _persist_log.warning("persistence.llm_client_failed", error=repr(exc))
    summary = record_run_memory(
        state, use_llm=use_llm, elapsed_sec=elapsed, client=client
    )
    evolved = summary.get("evolved")
    if evolved is not None:
        console.print(
            f"  [dim]Skill rewrite proposed for {evolved.name} "
            f"(v{evolved.version}) — pending human review[/dim]"
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

    from app.slack_setup import setup_slack_notifier
    from core.lifecycle import LifecycleEvent

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

    def _fire_prediction(st) -> None:
        """prediction.warning/alert quando o node preditivo cruzou o limiar."""
        pred = st.metadata.get("_prediction")
        level = getattr(pred, "event_level", None)
        if not level:
            return
        _fire(
            level,
            node_name="predictive_stage",
            cid=st.correlation_id,
            data={
                "event_level": level,
                "risk_score": pred.risk_score,
                "eta_minutes": pred.eta_minutes,
                "signals": pred.signals,
                "matched_history": getattr(pred, "matched_history", []),
            },
        )

    def _fire_diagnosis(st) -> None:
        """diagnosis.completed (V0: não dispara sem incidente)."""
        from application.use_cases.postmortem import _no_incident
        from blueprint.adapters.state_adapter import state_to_result

        if not st.diagnosis:
            return
        res = state_to_result(st)
        if _no_incident(res):
            return
        diag = res["stages"].get("diagnosis", {})
        action = (res["stages"].get("mitigation") or {}).get("action")
        _fire(
            "diagnosis.completed",
            cid=st.correlation_id,
            data={
                "root_cause": diag.get("root_cause") or "",
                "proposed_fix": diag.get("proposed_fix")
                or diag.get("fix_command")
                or (getattr(action, "command", None) if action else None)
                or "",
                "risk_level": st.safety_verdict or "unknown",
                "requires_human": bool(
                    res["stages"].get("safety_decision", {}).get("requires_human")
                ),
                "confidence": float(confidence_to_score(diag.get("confidence"))),
                "evidence": list(diag.get("evidence", []))[:5],
            },
        )

    # 1b. Tracker — resolvido antes do pipeline para falhar rápido (como o V0)
    tracker = _resolve_tracker(getattr(args, "tracker", None))

    # 2. Gera dados (simulados ou Databricks)
    scenario_name: str | None = None
    telemetry_stats: dict[str, int] = {}
    if getattr(args, "data_source", "simulated") == "databricks":
        sources = _create_databricks_sources()
        if sources is None:
            console.print(
                "[red]Cannot proceed without Databricks connection. Exiting.[/red]"
            )
            return EXIT_ERROR
        incident, scenario_name = step_fetch_databricks_data(
            *sources, stats=telemetry_stats
        )
    elif args.scenario:
        incident = get_scenario(args.scenario)(seed=42)
    else:
        incident = create_incident()

    # B8: validação na borda — cobre qualquer fonte (a de Databricks já
    # descartou os crus; aqui pegamos o que sobrou).
    telemetry_dropped = telemetry_stats.get(
        "telemetry_dropped", 0
    ) + drop_invalid_metric_points(incident)

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
    state = create_incident_state(
        args, incident_data, incident=incident, scenario_name=scenario_name
    )
    rebuild_rag = bool(getattr(args, "rebuild_rag", False))
    state = dataclasses.replace(
        state,
        metadata={
            **state.metadata,
            "_rebuild_rag": rebuild_rag,
            "_rag_cache_dir": RAG_CACHE_DIR,
            "_telemetry_dropped": telemetry_dropped,
        },
    )
    if rebuild_rag and args.no_rag:
        console.print(
            "[yellow]Warning: --rebuild-rag has no effect with --no-rag.[/yellow]"
        )

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
            state = dataclasses.replace(
                state, metadata={**state.metadata, "_rag_degraded": True}
            )

    # 6. Executa pipeline
    start_time = time.monotonic()
    exit_code = EXIT_SUCCESS
    aborted = False

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
        # Mostra o que o pipeline já tinha apurado (diagnóstico, postmortem),
        # não o estado inicial.
        aborted = True
        _aborted_state = _state_after_abort(exc, app_config)
        if _aborted_state is not None:
            state = _aborted_state
        _fire_prediction(state)
        _fire_diagnosis(state)
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
        # Pipeline completou. Ação que o PreValidator mandou para revisão
        # exige humano, como no V0 (exit 3).
        # B5: o estado terminal honesto decide o exit (regressão/rollback/erro
        # → 2 > aprovação pendente/NEEDS_REVIEW/NOT_VERIFIED → 3 > resolvido → 0).
        from blueprint.adapters.state_adapter import terminal_outcome

        exit_code = terminal_outcome(state).exit_code
        _fire_prediction(state)
        _fire_diagnosis(state)
        from application.use_cases.postmortem import _no_incident as _no_inc
        from blueprint.adapters.state_adapter import state_to_result as _to_result

        diagnosis = state.diagnosis or {}
        _conf_v1 = diagnosis.get("confidence", 0.0)
        _fire(
            "pipeline.completed",
            cid=_cid,
            data={
                "root_cause": diagnosis.get("root_cause") or "",
                "confidence": confidence_to_score(_conf_v1),
                "proposed_fix": diagnosis.get("proposed_fix")
                or diagnosis.get("fix_command", ""),
                "risk_level": state.safety_verdict or "unknown",
                "evidence": diagnosis.get("evidence", []),
                "total_duration_ms": 0,  # preenchido abaixo
                "no_incident": _no_inc(_to_result(state)),
            },
        )

    elapsed = time.monotonic() - start_time

    # 7. Exibe resultados
    display_results(state, compact=args.compact)

    # 7b. Memória episódica + aprendizado de skills (best-effort, como o V0)
    _persist_memory(state, args, elapsed)

    # 8. Tracking (opcional) e persistência do postmortem
    tracker_info = _save_outputs(state, args, tracker, aborted=aborted)

    # 9. Resumo final: tabela-resumo do V0 (print_summary) sobre o result do
    # adapter. Compression/sanitização/safety objetos não são capturados no
    # estado V1 — essas linhas ficam de fora, não inventadas.
    if state.diagnosis is not None:
        from blueprint.adapters.state_adapter import state_to_result

        print_summary(state_to_result(state), tracker_info=tracker_info)
    else:
        console.print(Rule("[bold]Summary[/bold]", style="cyan"))
    console.print(f"Total time: {elapsed:.1f}s")
    console.print(
        f"Exit code: {exit_code} ({EXIT_CODE_LABELS.get(exit_code, 'unknown')})"
    )

    log.info("main.completed", exit_code=exit_code, duration_seconds=round(elapsed, 1))

    return exit_code


__all__ = ["create_incident_state", "run_v1"]
