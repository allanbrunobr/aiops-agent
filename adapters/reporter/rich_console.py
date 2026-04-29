"""RichConsoleReporter — preserva o output visual da CLI (Fase 2c).

Mantem o formato de saida que existia antes da extracao do Reporter port:
arvore para detection, panel para diagnostico, table para mitigacao,
emojis e cores Rich para iteracoes ReAct.

Eventos sem mapeamento explicito sao silenciosamente ignorados — adapters
podem ser tolerantes a eventos novos enquanto a vocabulario evolui.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree

from application.ports.reporter import (
    DIAGNOSIS_COMPLETED,
    HUMAN_APPROVAL_REQUESTED,
    INCIDENT_DETECTED,
    ITERATION_STARTED,
    LLM_CALL_FAILED,
    MITIGATION_PROPOSED,
    THOUGHT_OBSERVED,
    TNR_VERIFIED,
    TOOL_CALLED,
    TOOL_OBSERVED,
)


class RichConsoleReporter:
    """Renderiza eventos como o ``Console`` original.

    Implementa o port ``Reporter`` via tipagem estrutural — sem herdar do
    Protocol, mantendo a mesma convencao de ``AnthropicLLMClient``.

    Aceita um ``Console`` injetado para facilitar testes que queiram
    capturar saida; default cria um novo.
    """

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    def emit(self, event: str, **payload: Any) -> None:
        handler = self._handlers.get(event)
        if handler is None:
            return
        handler(self, payload)

    # ------------------------------------------------------------------
    # Handlers — um por evento conhecido. Mantenha curtos e diretos.
    # ------------------------------------------------------------------

    def _on_incident_detected(self, p: dict[str, Any]) -> None:
        tree = Tree("[bold]Detection Analysis[/bold]")
        tree.add(
            f"Logs: {p['total_logs']} processed → {p['template_count']} templates (Drain3)"
        )
        tree.add(f"Metrics: {p['anomaly_count']} anomalies detected (3-sigma)")
        tree.add(f"Traces: {p['trace_count']} slow patterns identified (>p95)")
        if p.get("is_incident"):
            tree.add(
                f"[bold red]INCIDENT DETECTED[/bold red]: "
                f"{len(p.get('signals', []))} correlated signals"
            )
        else:
            tree.add("[green]No incident[/green]: insufficient correlated signals")
        self._console.print(tree)

    def _on_iteration_started(self, p: dict[str, Any]) -> None:
        self._console.print(
            f"\n  [dim]--- Iteration {p['current']}/{p['total']} ---[/dim]"
        )

    def _on_llm_call_failed(self, p: dict[str, Any]) -> None:
        self._console.print(
            "  [red]LLM call failed. Falling back to heuristic mode.[/red]"
        )

    def _on_thought_observed(self, p: dict[str, Any]) -> None:
        self._console.print(f"  💭 Thought: {p['text']}", style="italic")

    def _on_tool_called(self, p: dict[str, Any]) -> None:
        self._console.print(f"  🔧 Action: {p['tool_name']}({p['args']})")

    def _on_tool_observed(self, p: dict[str, Any]) -> None:
        self._console.print(f"  👁️ Observation: {p['result']}...")

    def _on_diagnosis_completed(self, p: dict[str, Any]) -> None:
        if p.get("mode") == "heuristic":
            self._console.print("  [yellow]Using heuristic fallback (no LLM)[/yellow]")
        evidence = p.get("evidence", [])
        self._console.print(
            Panel(
                f"[bold]Root Cause:[/bold] {p.get('root_cause', 'Unknown')}\n"
                f"[bold]Confidence:[/bold] {p.get('confidence', 'unknown')}\n"
                f"[bold]Evidence:[/bold] {', '.join(evidence[:3])}",
                title="Diagnosis Result",
                border_style="green",
            )
        )

    def _on_mitigation_proposed(self, p: dict[str, Any]) -> None:
        risk = p.get("risk_level", "MEDIUM")
        risk_color = "green" if risk == "LOW" else "yellow"
        table = Table(
            title="Proposed Mitigation", show_header=False, border_style="yellow"
        )
        table.add_row("Action", p.get("description", ""))
        table.add_row("Command", f"[bold]{p.get('command', '')}[/bold]")
        table.add_row("Risk", f"[{risk_color}]{risk}[/]")
        table.add_row("Rollback", p.get("rollback_command") or "N/A")
        table.add_row("Pattern", p.get("pattern_matched", "default"))
        self._console.print(table)

    def _on_human_approval_requested(self, p: dict[str, Any]) -> None:
        self._console.print(
            "  ⚠️  [bold yellow]Aguardando aprovação humana...[/bold yellow]"
        )

    def _on_tnr_verified(self, p: dict[str, Any]) -> None:
        self._console.print(f"\n  Pre-health:  {p.get('pre_snapshot')}", style="dim")
        self._console.print(f"  Post-health: {p.get('post_snapshot')}", style="dim")
        result_style = "green" if p.get("result") == "success" else "red"
        self._console.print(
            f"  Result: [{result_style}]{p.get('message', '')}[/{result_style}]"
        )

    _handlers: dict[str, Any] = {
        INCIDENT_DETECTED: _on_incident_detected,
        ITERATION_STARTED: _on_iteration_started,
        LLM_CALL_FAILED: _on_llm_call_failed,
        THOUGHT_OBSERVED: _on_thought_observed,
        TOOL_CALLED: _on_tool_called,
        TOOL_OBSERVED: _on_tool_observed,
        DIAGNOSIS_COMPLETED: _on_diagnosis_completed,
        MITIGATION_PROPOSED: _on_mitigation_proposed,
        HUMAN_APPROVAL_REQUESTED: _on_human_approval_requested,
        TNR_VERIFIED: _on_tnr_verified,
    }
