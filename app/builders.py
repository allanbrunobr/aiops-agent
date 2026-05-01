"""Builders compartilhados de subgrafos da pipeline.

Cada entrypoint (CLI, HTTP, MCP, demo) mantém sua própria composition root,
mas delega para estes builders quando há duplicação real entre eles.

Builders incluídos:
    - build_engine:   instancia BlueprintEngine com store/creds opcionais.
    - build_pipeline: re-export de build_incident_response_pipeline.
    - build_rag:      par (indexer, retriever) com configuração default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from blueprint.engine import BlueprintEngine
from blueprint.pipelines.incident_response import build_incident_response_pipeline
from rag.indexer import ObservabilityIndexer
from rag.retriever import ObservabilityRetriever

if TYPE_CHECKING:
    from application.ports.incident_store import IncidentStore
    from blueprint.nodes import PipelineDefinition
    from config.loader import AppConfig
    from application.ports.credential_provider import CredentialProvider


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


def build_rag() -> tuple[ObservabilityIndexer, ObservabilityRetriever]:
    """Cria par (indexer, retriever) com configuração padrão."""
    indexer = ObservabilityIndexer()
    retriever = ObservabilityRetriever(indexer)
    return indexer, retriever
