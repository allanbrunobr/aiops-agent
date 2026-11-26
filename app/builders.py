"""Builders compartilhados de subgrafos da pipeline.

Cada entrypoint (CLI, HTTP, MCP, demo) mantém sua própria composition root,
mas delega para estes builders quando há duplicação real entre eles.

Builders incluídos:
    - build_engine:             instancia BlueprintEngine com store/creds opcionais.
    - build_pipeline:           re-export de build_incident_response_pipeline.
    - build_rag:                par (indexer, retriever) conforme config.rag
                                (CoT + DomainReranker — F6-2).
    - build_reranker:           DomainReranker a partir de config.rag, ou None.
    - build_detector_agent:     DetectionAgent com thresholds de ``config``.
    - build_diagnoser_agent:    DiagnosisAgent com modelo de ``config.llm``.
    - build_agent_orchestrator: AgentOrchestrator (Estágio 2) com memória,
                                skill router e cliente LLM (plano de
                                refatoração, Fase 5 — F5-1).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from blueprint.engine import BlueprintEngine
from blueprint.pipelines.incident_response import build_incident_response_pipeline
from rag.indexer import ObservabilityIndexer
from rag.retriever import ObservabilityRetriever

if TYPE_CHECKING:
    from agents.detector import DetectionAgent
    from agents.diagnoser import DiagnosisAgent
    from agents.orchestrator import AgentOrchestrator
    from application.ports.credential_provider import CredentialProvider
    from application.ports.incident_store import IncidentStore
    from application.ports.llm_client import LLMClient
    from application.ports.reporter import Reporter
    from blueprint.nodes import PipelineDefinition
    from config.loader import AppConfig, RagConfig
    from rag.memory import IncidentMemoryDB
    from rag.reranker import DomainReranker


def build_engine(
    config: AppConfig,
    store: IncidentStore | None = None,
    creds: CredentialProvider | None = None,
) -> BlueprintEngine:
    """Instancia BlueprintEngine com dependências opcionais.

    store e creds são opcionais para preservar o comportamento atual de
    composition roots que não os passam (ex.: HTTP). Migrações que precisam
    desses recursos devem fornecê-los explicitamente.
    """
    return BlueprintEngine(config, store, creds)


def build_pipeline(config: AppConfig) -> PipelineDefinition:
    """Constrói o PipelineDefinition de incident response."""
    return build_incident_response_pipeline(config)


def build_reranker(config: AppConfig | None = None) -> DomainReranker | None:
    """DomainReranker a partir de ``config.rag`` (F6-2); ``None`` se desligado.

    Tier 1 (fusão de sinais) não carrega nada além do próprio módulo. Tier 2
    (cross-encoder) só com ``cross_encoder_enabled`` — baixa um modelo HF.
    """
    rag_cfg = _rag_config(config)
    if not rag_cfg.reranker_enabled:
        return None
    from rag.reranker import DomainReranker

    return DomainReranker(
        service_criticality_map=dict(rag_cfg.service_criticality_map),
        use_cross_encoder=rag_cfg.cross_encoder_enabled,
    )


def build_rag(
    config: AppConfig | None = None,
) -> tuple[ObservabilityIndexer, ObservabilityRetriever]:
    """Cria par (indexer, retriever) conforme ``config.rag``.

    - ``cot_enabled`` → ``CoTObservabilityRetriever`` (histórico de buscas +
      query expansion, consumido pelo ``DiagnosisAgent`` no tool
      ``search_similar``); senão ``ObservabilityRetriever``.
    - ``reranker_enabled`` → ``DomainReranker`` injetado no retriever.

    ``config=None`` mantém compat com os call sites antigos: usa ``RagConfig()``
    (defaults: reranker + CoT ligados, cross-encoder desligado).
    """
    rag_cfg = _rag_config(config)
    indexer = ObservabilityIndexer()
    reranker = build_reranker(config)
    if rag_cfg.cot_enabled:
        from rag.cot_retriever import CoTObservabilityRetriever

        return indexer, CoTObservabilityRetriever(indexer, reranker=reranker)
    return indexer, ObservabilityRetriever(indexer, reranker=reranker)


def _rag_config(config: AppConfig | None) -> RagConfig:
    from config.loader import RagConfig as _RagConfig

    if config is None:
        return _RagConfig()
    return getattr(config, "rag", None) or _RagConfig()


# ---------------------------------------------------------------------------
# Agentes (Fase 5 do plano de refatoração — F5-1)
#
# Imports de ``agents.*`` são lazy: este módulo é importado por composition
# roots que não usam agentes (webhook server, HTTP app) e os testes do V0
# patcham as classes no namespace de ``interfaces.cli.v0_pipeline``.
# ---------------------------------------------------------------------------


def resolve_llm_model(config: AppConfig) -> str:
    """Modelo LLM efetivo: env ``AIOPS_LLM_MODEL`` > ``config.llm.model_name``.

    Antes cada composition root repetia ``os.environ.get("AIOPS_LLM_MODEL",
    "claude-sonnet-4-20250514")`` com o default hardcoded; agora o default
    vem de ``LLMConfig`` (YAML/env via pydantic-settings).
    """
    return os.environ.get("AIOPS_LLM_MODEL") or config.llm.model_name


def build_detector_agent(
    config: AppConfig, *, reporter: Reporter | None = None
) -> DetectionAgent:
    """DetectionAgent com os thresholds de ``config.thresholds``.

    O ``AgentOrchestrator`` construía ``DetectionAgent()`` com defaults
    enquanto a CLI usava ``config`` — duas detecções com thresholds
    diferentes (F5-2). Com um único builder, V0 e V1 detectam igual.
    """
    from agents.detector import DetectionAgent

    return DetectionAgent(
        error_threshold=config.thresholds.error_threshold,
        sigma_threshold=config.thresholds.detection_sigma_threshold,
        reporter=reporter,
    )


def build_diagnoser_agent(
    config: AppConfig,
    *,
    max_iterations: int | None = None,
    similar_incidents: list | None = None,
    skill: object | None = None,
    reporter: Reporter | None = None,
) -> DiagnosisAgent:
    """DiagnosisAgent com o modelo resolvido por :func:`resolve_llm_model`."""
    from agents.diagnoser import DiagnosisAgent

    kwargs: dict = {
        "model": resolve_llm_model(config),
        "similar_incidents": similar_incidents,
        "skill": skill,
        "reporter": reporter,
    }
    if max_iterations is not None:
        kwargs["max_iterations"] = max_iterations
    return DiagnosisAgent(**kwargs)


def build_memory_db() -> IncidentMemoryDB:
    """``IncidentMemoryDB`` default (memória episódica + skills) da composition root."""
    from rag.memory import IncidentMemoryDB

    return IncidentMemoryDB()


def build_agent_orchestrator(
    config: AppConfig,
    *,
    client: LLMClient | None = None,
    memory_db: IncidentMemoryDB | None = None,
    reporter: Reporter | None = None,
    seed_skills: bool = True,
) -> AgentOrchestrator:
    """AgentOrchestrator (Estágio 2) com memória episódica e skill router.

    Args:
        client: ``LLMClient`` já construído. ``None`` cria o cliente Anthropic a
            partir de ``ANTHROPIC_API_KEY`` (comportamento dos dois call sites
            que este builder substitui).
        memory_db: ``IncidentMemoryDB`` compartilhado. ``None`` abre o default.
        seed_skills: semeia as skills padrão (idempotente) — desligue em testes
            que injetam um ``memory_db`` já populado.
    """
    from adapters.llm.anthropic_client import create_anthropic_llm_client
    from agents.orchestrator import AgentOrchestrator
    from agents.skill_router import SkillRouter
    from rag.memory import IncidentMemoryDB

    if memory_db is None:
        memory_db = IncidentMemoryDB()
    skill_router = SkillRouter(memory_db)
    if seed_skills:
        skill_router.seed_default_skills()
    if client is None:
        client = create_anthropic_llm_client(
            api_key=os.environ.get("ANTHROPIC_API_KEY", "")
        )
    # F6-3 (NFR6): orçamento de chamadas aplicado onde elas acontecem. Antes,
    # BlueprintEngine.increment_llm_calls existia mas ninguém o chamava.
    from adapters.llm.counting_client import CountingLLMClient

    max_calls = getattr(getattr(config, "blueprint", None), "max_llm_calls", 15)
    if not isinstance(client, CountingLLMClient):
        client = CountingLLMClient(client, max_calls=max_calls)
    return AgentOrchestrator(
        client=client,
        model=resolve_llm_model(config),
        memory_db=memory_db,
        skill_router=skill_router,
        config=config,
        reporter=reporter,
    )
