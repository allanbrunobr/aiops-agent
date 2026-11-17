"""Etapa 5 da CLI: salva e exibe o postmortem.

Casca de I/O + Rich sobre o caso de uso puro
``application.use_cases.postmortem``. Assinatura idêntica à função que vivia
em ``main.py`` — cinco arquivos de teste a chamam diretamente.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule

from application.use_cases.postmortem import (
    json_default,
    render_postmortem_markdown,
    serialize_result,
)
from interfaces.cli.console import console


def step_postmortem(
    result: dict[str, Any],
    incident: Any,
    output_dir: str = "output",
    output_format: str = "markdown",
    tracker_url: str | None = None,
    tracker_issue_key: str | None = None,
) -> tuple[Path | None, Path | None]:
    """Etapa 5: Gera postmortem em markdown e/ou JSON."""
    console.print(Rule("[bold]Stage 5: Postmortem Generation[/bold]", style="cyan"))
    console.print()

    now = datetime.now()
    postmortem = render_postmortem_markdown(
        result,
        incident,
        now=now,
        tracker_url=tracker_url,
        tracker_issue_key=tracker_issue_key,
    )

    # Salva postmortem nos formatos solicitados
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    timestamp_str = now.strftime("%Y-%m-%d_%H%M%S")

    markdown_path = None
    json_path = None

    # Markdown output
    if output_format in ("markdown", "both"):
        markdown_path = out / f"postmortem_{timestamp_str}.md"
        markdown_path.write_text(postmortem)
        console.print(f"  Postmortem (markdown) saved to: [bold]{markdown_path}[/bold]")

    # JSON output
    if output_format in ("json", "both"):
        serialized = serialize_result(result, incident)
        json_path = out / f"postmortem_{timestamp_str}.json"
        json_path.write_text(
            json.dumps(serialized, default=json_default, indent=2, ensure_ascii=False)
        )
        console.print(f"  Postmortem (JSON) saved to: [bold]{json_path}[/bold]")

    # Preview (mostra markdown se disponivel)
    if output_format in ("markdown", "both"):
        console.print()
        console.print(
            Panel(
                Markdown(postmortem[:1500] + "\n\n*... (truncated for display)*"),
                title="Postmortem Preview",
                border_style="dim",
            )
        )

    return markdown_path, json_path


__all__ = ["step_postmortem"]
