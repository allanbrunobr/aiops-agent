"""Trackers de incidente da CLI (``--tracker``, Story 6.3).

Registry de factories + etapas de tracking. Import de ``tools.jira_tracker``
é lazy para não carregar ``requests`` quando o tracker não é usado.
Sobrevive à remoção do V0 (F5-4): o V1 ainda não expõe ``--tracker``.
"""

from __future__ import annotations

from typing import Any, Callable

from rich.rule import Rule

from application.use_cases.postmortem import get_action_items
from interfaces.cli.console import console


def _create_jira_tracker() -> Any | None:
    """Factory que cria JiraIncidentTracker a partir de env vars.

    Lazy-import para evitar carregar requests quando tracker nao e usado.
    Retorna instancia de JiraIncidentTracker ou None se configuracao ausente.
    """
    try:
        from tools.jira_tracker import JiraIncidentTracker

        return JiraIncidentTracker()
    except Exception as e:
        console.print(
            f"[yellow]Warning: Could not initialize Jira tracker: {e}[/yellow]"
        )
        return None


TRACKER_REGISTRY: dict[str, Callable[[], Any | None]] = {
    "jira": _create_jira_tracker,
}


def _resolve_tracker(tracker_name: str | None) -> Any | None:
    """Resolve nome de tracker para instancia de IncidentTracker.

    Retorna None se tracker_name e None ou "none".
    Retorna instancia do tracker se factory funcionar, ou None com warning.
    """
    if tracker_name is None or tracker_name == "none":
        return None

    factory = TRACKER_REGISTRY.get(tracker_name)
    if factory is None:
        console.print(
            f"[yellow]Warning: Unknown tracker '{tracker_name}'. Skipping.[/yellow]"
        )
        return None

    return factory()


def step_tracking(result: dict[str, Any], tracker: Any) -> str | None:
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
        console.print(
            f"[yellow]  Warning: Tracker incident creation failed: {e}[/yellow]"
        )
        console.print(
            "[dim]  Pipeline continues — postmortem will still be generated.[/dim]"
        )
        return None


def _attach_postmortem_to_tracker(
    tracker: Any, issue_key: str, result: dict[str, Any]
) -> str | None:
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
        timeline.append(
            {
                "time": entry.get("time", "?"),
                "event": f"{entry.get('from', '?')} -> {entry.get('to', '?')}: {entry.get('reason', '')}",
            }
        )

    # Action items do pattern matched
    pattern_matched = (
        mitigation.get("pattern_matched", "default") if mitigation else "default"
    )
    action_items = get_action_items(pattern_matched)

    try:
        comment_id = tracker.create_postmortem(
            issue_key, timeline, root_cause, action_items
        )
        console.print(f"  Postmortem attached to {issue_key} (comment: {comment_id})")
        return comment_id
    except Exception as e:
        console.print(f"[yellow]  Warning: Postmortem attachment failed: {e}[/yellow]")
        return None


__all__ = [
    "TRACKER_REGISTRY",
    "_create_jira_tracker",
    "_resolve_tracker",
    "step_tracking",
    "_attach_postmortem_to_tracker",
]
