from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge"])


class PatternCreateRequest(BaseModel):
    """Schema de validação para criação de padrões de incidente."""

    service: str = Field(..., min_length=1, description="Service name")
    symptoms: list[str] = Field(..., min_length=1, description="Observable symptoms")
    root_cause: str = Field(..., min_length=1, description="Root cause description")
    resolution: str = Field(..., min_length=1, description="Resolution steps")
    tags: list[str] = Field(default_factory=list, description="Classification tags")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Confidence score"
    )
    source_incident_id: str | None = Field(
        default=None, description="Source incident correlation_id"
    )

    @field_validator("symptoms")
    @classmethod
    def symptoms_not_empty(cls, v: list[str]) -> list[str]:
        """Valida que cada sintoma não é vazio."""
        if any(not s.strip() for s in v):
            raise ValueError("Each symptom must be a non-empty string")
        return v


class PatternCreateResponse(BaseModel):
    """Resposta para criação de padrão."""

    pattern_id: str
    status: str = "created"
    duplicate_warning: str | None = None


def get_knowledge_store():
    """Placeholder — substituído pelo composition root em server/app.py."""
    raise NotImplementedError("Store must be injected via app.dependency_overrides")


@router.post("/patterns", response_model=PatternCreateResponse, status_code=201)
async def create_pattern(
    request: PatternCreateRequest,
    knowledge_store=Depends(get_knowledge_store),
) -> PatternCreateResponse:
    """Adiciona novo padrão de incidente à base de conhecimento.

    Valida schema, persiste padrão e marca índice FAISS como stale.
    Rebuild do índice ocorre em background (lazy rebuild, decisão 1b).
    """
    pattern_id = f"pat-{uuid.uuid4().hex[:12]}"

    # Verifica duplicatas (mesmo serviço + causa raiz)
    duplicate_warning = None
    existing = knowledge_store.find_by_service_and_cause(
        request.service, request.root_cause,
    )
    if existing:
        duplicate_warning = (
            f"Similar pattern exists: {existing.pattern_id} "
            f"(same service '{request.service}' and root cause)"
        )
        logger.warning(
            "knowledge.duplicate_pattern",
            extra={
                "new_pattern_id": pattern_id,
                "existing_pattern_id": existing.pattern_id,
                "service": request.service,
            },
        )

    from rag.models import KnowledgePattern

    pattern = KnowledgePattern(
        pattern_id=pattern_id,
        service=request.service,
        symptoms=request.symptoms,
        root_cause=request.root_cause,
        resolution=request.resolution,
        tags=request.tags,
        confidence=request.confidence,
    )

    knowledge_store.add_pattern(pattern)

    logger.info(
        "knowledge.pattern_added",
        extra={"pattern_id": pattern_id, "service": request.service},
    )

    return PatternCreateResponse(
        pattern_id=pattern_id,
        duplicate_warning=duplicate_warning,
    )


@router.get("/patterns")
async def list_patterns(
    knowledge_store=Depends(get_knowledge_store),
) -> dict:
    """Lista todos os padrões na base de conhecimento."""
    patterns = knowledge_store.get_all_patterns()
    return {
        "total": len(patterns),
        "patterns": [
            {
                "pattern_id": p.pattern_id,
                "service": p.service,
                "symptoms": p.symptoms,
                "root_cause": p.root_cause,
                "tags": p.tags,
                "confidence": p.confidence,
                "is_negative_example": p.is_negative_example,
                "created_at": p.created_at,
            }
            for p in patterns
        ],
    }
