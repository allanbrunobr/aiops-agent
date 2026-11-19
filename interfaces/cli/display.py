"""Apresentação Rich da CLI: banner, resumo (V0) e resultados (V1).

Puro Rich sobre o ``console`` único — nenhuma lógica de negócio. Extraído de
``main.py`` (F5-2) sem alteração de texto; ``print_summary`` é V0-only e
F5-4 decide se adapta ao V1 ou remove.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from application.use_cases.postmortem import _no_incident
from interfaces.cli.console import console

if TYPE_CHECKING:
    from domain.incident_state import IncidentState


def print_banner() -> None:
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


def _requires_human(safety_result: Any, remediation_result: Any) -> bool:
    """Aprovação humana considerando TODAS as camadas pré-execução da CLI.

    Uma camada que aprova não anula a que bloqueia: o PreExecutionValidator
    pode auto-aprovar um comando que o VersionValidator classifica como
    MALICIOUS (ex.: `kubectl rollout restart deployment/postgres:9.4`).
    """
    if safety_result is not None and safety_result.requires_human:
        return True
    if remediation_result is not None and not remediation_result.is_safe:
        return True
    return False


def print_summary(
    result: dict[str, Any],
    compression_stats: Any = None,
    safety_result: Any = None,
    san_report: Any = None,
    remediation_result: Any = None,
    tracker_info: dict[str, Any] | None = None,
) -> None:
    """Exibe resumo final."""
    console.print()
    console.print(Rule("[bold]Summary[/bold]", style="cyan"))
    console.print()

    success = result.get("success", False)
    awaiting_human = _requires_human(safety_result, remediation_result)
    if awaiting_human:
        status_icon = "[bold yellow]AWAITING HUMAN[/bold yellow]"
    elif _no_incident(result):
        # Janela saudável sai com exit 0; UNRESOLVED em vermelho contradizia.
        status_icon = "[bold green]NO INCIDENT[/bold green]"
    elif success:
        status_icon = "[bold green]RESOLVED[/bold green]"
    else:
        status_icon = "[bold red]UNRESOLVED[/bold red]"

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
        "[green]Incident detected[/green]"
        if detection.get("is_incident")
        else "[yellow]No incident[/yellow]",
        f"{len(detection.get('signals', []))} correlated signals",
    )

    # Context Compression (Acon + AOI)
    if compression_stats:
        ratio = max(compression_stats.compression_ratio, 0)
        # critical_preserved can be 100.0 (meaning 100%) — normalize
        crit_pres = min(compression_stats.critical_preserved, 100) / 100
        table.add_row(
            "Compression",
            f"[magenta]{ratio:.0%} reduction[/magenta]"
            if ratio > 0
            else "[magenta]within budget[/magenta]",
            f"{compression_stats.chunks_kept} chunks kept, {compression_stats.chunks_removed} removed, "
            f"{crit_pres:.0%} critical preserved",
        )

    # Diagnosis
    diagnosis = result.get("stages", {}).get("diagnosis", {})
    llm_model = result.get("llm_model", "unknown")
    table.add_row(
        "Diagnosis",
        f"[green]{diagnosis.get('confidence', '?')} confidence[/green]",
        f"{(diagnosis.get('root_cause') or 'N/A')[:60]} [dim](model: {llm_model})[/dim]",
    )

    # Mitigation
    mitigation = result.get("stages", {}).get("mitigation", {})
    if mitigation:
        pattern = mitigation.get("pattern_matched", "N/A")
        table.add_row("Mitigation", f"Pattern: {pattern}", "Awaiting human approval")

    # Safety Validation (Pre-Validator)
    if safety_result:
        risk_colors = {
            "LOW": "green",
            "MEDIUM": "yellow",
            "HIGH": "red",
            "CRITICAL": "bold red",
        }
        risk_color = risk_colors.get(safety_result.risk_level.value, "white")
        table.add_row(
            "Safety Check",
            f"[{risk_color}]{safety_result.risk_level.value} risk ({safety_result.risk_score:.0f}/100)[/{risk_color}]",
            f"{safety_result.approval_status.value} | "
            f"{'requires human ⚠️' if safety_result.requires_human else 'auto-approved ✅'}",
        )

    # Remediation Validation (VersionValidator)
    if remediation_result:
        rem_colors = {
            "SAFE": "green",
            "CAUTION": "yellow",
            "DANGEROUS": "red",
            "MALICIOUS": "bold red",
        }
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
        risk_color = (
            "red"
            if prediction.risk_score >= 60
            else "yellow"
            if prediction.risk_score >= 30
            else "green"
        )
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
        style = {
            "success": "green",
            "not_verified": "yellow",
            "regression": "red",
        }.get(tnr_result, "red")
        table.add_row(
            "TNR Verify",
            f"[{style}]{tnr_result}[/{style}]",
            verification.get("message", "")[:80],
        )

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
        elif _no_incident(result):
            # step_tracking pulou de propósito: não é erro do tracker.
            table.add_row("Tracking", "[dim]not created[/dim]", "no incident detected")
        else:
            table.add_row(
                "Tracking",
                "[yellow]skipped[/yellow]",
                "tracker error — postmortem still generated",
            )

    table.add_row(
        "Status", status_icon, f"{len(result.get('timeline', []))} state transitions"
    )
    console.print(table)

    # Timeline visual
    console.print("\n  [bold]State Machine Timeline:[/bold]")
    for t in result.get("timeline", []):
        icon = {
            "DETECTING": "->",
            "DIAGNOSING": "->",
            "MITIGATING": "->",
            "VERIFYING": "->",
            "RESOLVED": "->",
            "ROLLBACK": "<-",
            "FAILED": "!!",
        }.get(t["to"], "->")
        console.print(
            f"    {t['time']} {icon} {t['from']} -> [bold]{t['to']}[/bold]: {t['reason'][:60]}"
        )


def display_results(state: IncidentState, compact: bool = False) -> None:
    """Exibe resultados do pipeline Blueprint Engine via rich.

    Preserva o formato visual de V0 (Panel, Table, etc).
    """
    # Diagnostico
    diagnosis = state.diagnosis or {}
    if diagnosis:
        console.print(
            Panel(
                f"[bold]Root Cause:[/bold] {diagnosis.get('root_cause', 'Unknown')}\n"
                f"[bold]Confidence:[/bold] {diagnosis.get('confidence', 'unknown')}\n"
                f"[bold]Evidence:[/bold] {', '.join(diagnosis.get('evidence', [])[:3])}",
                title="Diagnosis Result",
                border_style="green",
            )
        )

    # Safety
    if state.safety_verdict:
        style = "green" if state.safety_verdict == "APPROVED" else "red"
        console.print(
            Panel(
                f"[bold]Verdict:[/bold] {state.safety_verdict}\n"
                f"[bold]Reasons:[/bold] {'; '.join(state.safety_reasons)}",
                title="Safety Verification",
                border_style=style,
            )
        )

    # Postmortem
    if state.postmortem_md and not compact:
        console.print(Rule("[bold]Postmortem[/bold]", style="cyan"))
        console.print(state.postmortem_md)


def save_postmortem_v1(postmortem_md: str, output_dir: str = "output") -> Path | None:
    """Salva postmortem em arquivo markdown."""
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)
    filename = f"postmortem_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.md"
    path = output_path / filename
    path.write_text(postmortem_md)
    console.print(f"[green]Postmortem saved: {path}[/green]")
    return path


__all__ = [
    "_requires_human",
    "print_banner",
    "print_summary",
    "display_results",
    "save_postmortem_v1",
]
