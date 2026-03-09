from __future__ import annotations

import datetime
from dataclasses import dataclass, field


@dataclass
class KnowledgePattern:
    """Padrão de incidente para a base de conhecimento RAG."""

    pattern_id: str
    service: str
    symptoms: list[str]
    root_cause: str
    resolution: str
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    is_negative_example: bool = False
    created_at: str = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    def to_embedding_text(self) -> str:
        """Gera texto para embedding combinando sintomas, causa raiz e serviço."""
        symptoms_text = "; ".join(self.symptoms)
        return (
            f"Service: {self.service}. "
            f"Symptoms: {symptoms_text}. "
            f"Root cause: {self.root_cause}. "
            f"Resolution: {self.resolution}."
        )


@dataclass
class KnowledgeSearchResult:
    """Resultado de busca na base de conhecimento."""

    pattern: KnowledgePattern
    similarity_score: float

    def to_llm_context(self) -> str:
        """Formata resultado para contexto LLM."""
        return (
            f"[Similar Incident] (similarity={self.similarity_score:.3f}, "
            f"confidence={self.pattern.confidence:.2f})\n"
            f"  Service: {self.pattern.service}\n"
            f"  Root Cause: {self.pattern.root_cause}\n"
            f"  Resolution: {self.pattern.resolution}\n"
            f"  Tags: {', '.join(self.pattern.tags)}"
        )
