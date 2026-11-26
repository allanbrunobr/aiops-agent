"""Parser de argumentos da CLI e validação de exclusão mútua.

Separado do entrypoint para permitir testes sem executar o pipeline.
O Blueprint Engine (V1) é o único engine; ``--pipeline-v1`` sobrevive como
alias deprecated sem efeito (F5-4 P6).
"""

from __future__ import annotations

import argparse
import sys

from data.registry import list_scenarios
from interfaces.cli.exit_codes import EXIT_ERROR

# Nomes de tracker aceitos por --tracker. A factory concreta de cada um vive
# em interfaces/cli/trackers.py (lazy import de requests/Jira); aqui só o
# contrato de CLI, para que o parser não arraste dependências de rede.
TRACKER_CHOICES: tuple[str, ...] = ("jira",)


class _CliParser(argparse.ArgumentParser):
    """Erros de argumento saem com código 1. O código 2 fica para regressão do TNR."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(EXIT_ERROR, f"{self.prog}: error: {message}\n")


def build_arg_parser() -> argparse.ArgumentParser:
    """Cria o parser de argumentos CLI.

    Separado de main() para permitir testes sem executar o pipeline.
    """
    parser = _CliParser(
        description="AIOps Agent - Agentic Incident Response Demo",
        epilog="""Exit codes:
  0  Pipeline resolved successfully (TNR verified, no human gate left)
  1  Invalid arguments, or an unrecoverable error in a stage
  2  TNR regression detected (post-health worse than pre-health)
  3  Safety blocked action (human approval required)""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--no-llm", action="store_true", help="Skip LLM calls (heuristic mode)"
    )
    parser.add_argument("--compact", action="store_true", help="Compact output")
    parser.add_argument(
        "--no-rag", action="store_true", help="Skip RAG indexing (faster)"
    )
    parser.add_argument(
        "--pipeline-v1",
        action="store_true",
        help="DEPRECATED no-op: the Blueprint Engine (V1) is now the only pipeline",
    )
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
        choices=list(TRACKER_CHOICES) + ["none"],
        default=None,
        metavar="TRACKER",
        help=(
            "Incident tracker for auto-creating issues after diagnosis. "
            "Choices: jira, none. Default: omitted (no tracking, backward-compatible). "
            "'none' explicitly disables tracking."
        ),
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="Continuous monitoring mode (Vigia Barato); requires --source",
    )
    parser.add_argument(
        "--source",
        type=str,
        default=None,
        metavar="URL",
        help="Metrics source for --monitor: csv://<path> or prometheus://<url>",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="Seconds between monitor ticks (> 0, default: 60)",
    )
    parser.add_argument(
        "--ticks",
        type=int,
        default=None,
        metavar="N",
        help="Run N monitor ticks and exit (default: loop forever)",
    )
    parser.add_argument(
        "--monitor-service",
        type=str,
        default="hpa-demo",
        metavar="NAME",
        help="Service name used when the monitor escalates (default: hpa-demo)",
    )
    parser.add_argument(
        "--profile",
        choices=["auto", "hpa", "generic"],
        default="auto",
        help=(
            "Monitor tuning profile: auto = detect HPA by metric name in a CSV "
            "replay (default); hpa = force HPA tuning; generic = never apply "
            "HPA tuning"
        ),
    )
    return parser


MONITOR_SOURCE_SCHEMES: tuple[str, ...] = ("csv", "prometheus")


def _validate_monitor_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    """Regras do modo --monitor (modo próprio, exclusivo com os demais)."""
    conflicts = [
        flag
        for flag, active in (
            ("--scenario", args.scenario is not None),
            ("--data-source databricks", args.data_source == "databricks"),
            ("--tracker", args.tracker is not None),
        )
        if active
    ]
    if conflicts:
        parser.error(f"--monitor cannot be combined with {', '.join(conflicts)}")
    if not args.source:
        parser.error("--monitor requires --source (csv://<path> or prometheus://<url>)")
    scheme, sep, rest = args.source.partition("://")
    if not sep or scheme not in MONITOR_SOURCE_SCHEMES or not rest:
        parser.error(
            f"invalid --source {args.source!r}: expected csv://<path> or prometheus://<url>"
        )
    if args.interval <= 0:
        parser.error("--interval must be > 0")
    if args.ticks is not None and args.ticks <= 0:
        parser.error("--ticks must be > 0")


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """Valida regras de exclusao mutua entre argumentos CLI.

    Regras:
      --data-source databricks + --scenario sao mutuamente exclusivos.
      Dados reais nao possuem cenarios simulados.
    """
    if args.monitor:
        _validate_monitor_args(parser, args)
        return
    if args.data_source == "databricks" and args.scenario is not None:
        parser.error(
            "--data-source databricks cannot be combined with --scenario (real data has no simulated scenarios)"
        )


__all__ = ["TRACKER_CHOICES", "_CliParser", "build_arg_parser", "_validate_args"]
