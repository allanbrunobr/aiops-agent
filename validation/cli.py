from __future__ import annotations

"""
Ponto de entrada CLI para validação de subsistemas.

Uso: python -m aiops validate --subsystem knowledge
"""

import argparse
import sys

from rich.console import Console
from rich.table import Table


def main() -> None:
    """Executa validação via CLI com saída rich."""
    parser = argparse.ArgumentParser(description="AIOps subsystem validation")
    parser.add_argument(
        "--subsystem",
        choices=["knowledge"],
        default="knowledge",
        help="Subsystem to validate",
    )
    args = parser.parse_args()

    console = Console()

    if args.subsystem == "knowledge":
        from validation.runner import ValidationRunner
        from validation.scenarios.rag_retrieval import RAGRetrievalScenario
        from validation.scenarios.rag_degradation import RAGDegradationScenario
        from validation.scenarios.knowledge_write_read import KnowledgeWriteReadScenario

        runner = ValidationRunner(subsystem="knowledge")
        runner.register(RAGRetrievalScenario())
        runner.register(RAGDegradationScenario())
        runner.register(KnowledgeWriteReadScenario())
    else:
        console.print(f"[red]Unknown subsystem: {args.subsystem}[/red]")
        sys.exit(1)

    console.print(f"\n[bold]Running {args.subsystem} validation...[/bold]\n")
    report = runner.run_all()

    # Tabela de resultados
    table = Table(title=f"{args.subsystem} Validation Results")
    table.add_column("Scenario", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Duration (ms)", justify="right")
    table.add_column("Details")

    for result in report.results:
        status = "[green]PASSED[/green]" if result.passed else "[red]FAILED[/red]"
        table.add_row(
            result.name,
            status,
            f"{result.duration_ms:.0f}",
            result.details[:80],
        )

    console.print(table)

    summary_color = "green" if report.failed == 0 else "red"
    console.print(f"\n[{summary_color}]{report.summary()}[/{summary_color}]\n")

    sys.exit(0 if report.failed == 0 else 1)


if __name__ == "__main__":
    main()
