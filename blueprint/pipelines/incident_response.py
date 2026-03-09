"""Definicao do pipeline de incident response com safety layers.

Registra todas as etapas de processamento e safety como BlueprintNodes
com tipos explicitos e handlers de falha.

Safety invariant: TODOS os 4 safety layers executam antes de qualquer action proposal.
"""
from __future__ import annotations

from config.loader import AppConfig
from blueprint.nodes import (
    BlueprintNode,
    NodeType,
    OnFailure,
    PipelineDefinition,
)
from blueprint.adapters.pipeline_nodes import (
    make_aggregator_node,
    make_context_compressor_node,
    make_diagnoser_node,
    make_diagnoser_fallback,
    make_postmortem_node,
    make_postmortem_fallback,
)
from blueprint.adapters.safety_nodes import (
    make_sanitizer_node,
    make_pre_validator_node,
    make_tnr_node,
    make_version_validator_node,
)


def build_incident_response_pipeline(config: AppConfig) -> PipelineDefinition:
    """Constroi a definicao do pipeline de incident response com safety layers.

    Ordem de execucao (8 nodes):
      1. sanitizer       (safety/deterministic) — BEFORE any processing
      2. aggregator      (deterministic) — LogParser + MetricFilter + TraceFilter
      3. context_compressor (deterministic) — Compressao de contexto
      4. diagnoser       (agentic) — ReAct loop com Claude + fallback heuristico
      5. version_validator (safety/deterministic) — CVE/version check on remediation
      6. pre_validator    (safety/deterministic) — Blast radius + blocklist
      7. tnr              (safety/deterministic) — Pre/post health comparison
      8. postmortem       (agentic) — Geracao de postmortem

    Safety invariant: ALL 4 safety layers execute before any action proposal.
      - sanitizer: before ALL processing (Layer 1)
      - version_validator: after diagnosis, validates remediation (Layer 4)
      - pre_validator: after version check, validates action safety (Layer 2)
      - tnr: after pre-validation, verifies health impact (Layer 3)

    Args:
        config: Configuracao da aplicacao.

    Returns:
        PipelineDefinition com nodes registrados em ordem.
    """
    nodes = [
        # === Safety Layer 1: Sanitization (BEFORE any processing) ===
        BlueprintNode(
            id="sanitizer",
            node_type=NodeType.DETERMINISTIC,
            execute=make_sanitizer_node(config),
            on_failure=OnFailure.ABORT,
            description="AIOpsShield: sanitize telemetry against adversarial payloads",
        ),

        # === Deterministic Processing ===
        BlueprintNode(
            id="aggregator",
            node_type=NodeType.DETERMINISTIC,
            execute=make_aggregator_node(config),
            on_failure=OnFailure.ABORT,
            description="Drain3 log parsing + 3-sigma metric filter + p95 trace filter",
        ),
        BlueprintNode(
            id="context_compressor",
            node_type=NodeType.DETERMINISTIC,
            execute=make_context_compressor_node(config),
            on_failure=OnFailure.SKIP,
            description="Acon+AOI context compression for LLM token optimization",
        ),

        # === Agentic Processing ===
        BlueprintNode(
            id="diagnoser",
            node_type=NodeType.AGENTIC,
            execute=make_diagnoser_node(config),
            on_failure=OnFailure.FALLBACK,
            fallback=make_diagnoser_fallback(config),
            description="ReAct loop with Claude for root cause analysis",
        ),

        # === Safety Layers 2-4: Post-diagnosis validation ===
        BlueprintNode(
            id="version_validator",
            node_type=NodeType.DETERMINISTIC,
            execute=make_version_validator_node(config),
            on_failure=OnFailure.ABORT,
            description="CVE/version safety check on proposed remediations",
        ),
        BlueprintNode(
            id="pre_validator",
            node_type=NodeType.DETERMINISTIC,
            execute=make_pre_validator_node(config),
            on_failure=OnFailure.ABORT,
            description="Blast radius and command blocklist validation",
        ),
        BlueprintNode(
            id="tnr",
            node_type=NodeType.DETERMINISTIC,
            execute=make_tnr_node(config),
            on_failure=OnFailure.ABORT,
            description="Transactional No-Regression health verification",
        ),

        # === Postmortem (after all safety checks) ===
        BlueprintNode(
            id="postmortem",
            node_type=NodeType.AGENTIC,
            execute=make_postmortem_node(config),
            on_failure=OnFailure.FALLBACK,
            fallback=make_postmortem_fallback(config),
            description="Postmortem generation from diagnosis results",
        ),
    ]

    return PipelineDefinition(
        name="incident_response",
        nodes=nodes,
        description="Full incident response pipeline with 4-layer safety architecture",
    )
