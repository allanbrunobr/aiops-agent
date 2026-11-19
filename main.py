"""
AIOps Agent - entrypoint da CLI.

O conteúdo vive em ``interfaces/cli/`` (args, console, display, trackers,
data_sources, postmortem, v0_pipeline, v1_runner); este arquivo só faz parse,
carrega o ambiente e despacha para o engine escolhido.

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

import os
import sys

# ---------------------------------------------------------------------------
# Compat (F5-2): nomes que a suíte de testes ainda importa de ``main``.
# Os módulos definitivos são ``interfaces/cli``, ``core`` e ``application``;
# este bloco inteiro é removido em F5-4 junto com o engine V0.
# Cada linha tem o próprio ``noqa: F401`` — sem isso ``ruff --fix`` apaga.
# ---------------------------------------------------------------------------
from application.use_cases.postmortem import (
    ACTION_ITEMS_MAP,  # noqa: F401
    _no_incident,  # noqa: F401
    _postmortem_verdict,  # noqa: F401
)
from application.use_cases.postmortem import get_action_items as _get_action_items  # noqa: F401
from application.use_cases.postmortem import json_default as _json_default  # noqa: F401
from application.use_cases.postmortem import (
    serialize_health_snapshot as _serialize_health_snapshot,  # noqa: F401
)
from application.use_cases.postmortem import serialize_result as _serialize_result  # noqa: F401

# Imports usados por main()
from config.env import load_local_env
from core.secrets import API_KEY_PATTERNS  # noqa: F401
from core.secrets import contains_api_key_pattern as _contains_api_key_pattern  # noqa: F401
from core.secrets import sanitize_secrets as _sanitize_secrets  # noqa: F401
from data.sample_incident import create_incident  # noqa: F401
from interfaces.cli.args import (
    _CliParser,  # noqa: F401
    _validate_args,
    build_arg_parser,
)
from interfaces.cli.console import console
from interfaces.cli.data_sources import (
    _create_databricks_sources,  # noqa: F401
    step_fetch_databricks_data,  # noqa: F401
)
from interfaces.cli.display import (
    _requires_human,  # noqa: F401
    display_results,  # noqa: F401
    print_banner,  # noqa: F401
    print_summary,  # noqa: F401
)
from interfaces.cli.exit_codes import (
    EXIT_APPROVAL_NEEDED,  # noqa: F401
    EXIT_CODE_LABELS,  # noqa: F401
    EXIT_ERROR,  # noqa: F401
    EXIT_REGRESSION,  # noqa: F401
    EXIT_SUCCESS,  # noqa: F401
)
from interfaces.cli.postmortem import step_postmortem  # noqa: F401
from interfaces.cli.trackers import (
    TRACKER_REGISTRY,  # noqa: F401
    _attach_postmortem_to_tracker,  # noqa: F401
    _create_jira_tracker,  # noqa: F401
    _resolve_tracker,  # noqa: F401
    step_tracking,  # noqa: F401
)
from interfaces.cli.v0_pipeline import (
    _extract_fault_tags,  # noqa: F401
    _persist_log,  # noqa: F401
    _record_safety_decision,  # noqa: F401
    run_v0,
    step_agents,  # noqa: F401
    step_compression,  # noqa: F401
    step_generate_data,  # noqa: F401
    step_persist_incident,  # noqa: F401
    step_pipeline,  # noqa: F401
    step_predict,  # noqa: F401
    step_rag,  # noqa: F401
    step_remediation_validation,  # noqa: F401
    step_safety_validation,  # noqa: F401
    step_sanitize,  # noqa: F401
    step_skill_learning,  # noqa: F401
)
from interfaces.cli.v1_runner import create_incident_state  # noqa: F401
from interfaces.cli.v1_runner import run_v1 as main_v1


def main() -> int:
    """Entry point principal. Retorna exit code baseado no resultado do pipeline.

    O pipeline sequencial V0 (step_*) é o default; ``--pipeline-v1`` delega ao
    Blueprint Engine via ``main_v1``. Databricks e tracker só existem no V0.
    """
    parser = build_arg_parser()
    args = parser.parse_args()

    # Validate mutual exclusion rules
    _validate_args(parser, args)

    # Load .env e .env.local (loader único — F5-3)
    load_local_env()

    # Logging estruturado; nível via AIOPS_LOG_LEVEL (default WARNING)
    from core.logging import configure_logging

    configure_logging(os.environ.get("AIOPS_LOG_LEVEL", "WARNING"))

    # Verifica API key se vai usar LLM
    use_llm = not args.no_llm
    if use_llm and not os.environ.get("ANTHROPIC_API_KEY"):
        console.print(
            "[yellow]ANTHROPIC_API_KEY not found. Running in heuristic mode (--no-llm).[/yellow]"
        )
        console.print(
            "[dim]Set ANTHROPIC_API_KEY in .env for full ReAct diagnosis.[/dim]\n"
        )
        use_llm = False
        args.no_llm = True

    if getattr(args, "pipeline_v1", False):
        return main_v1(args)

    return run_v0(args)


if __name__ == "__main__":
    sys.exit(main())
