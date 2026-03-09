from __future__ import annotations

import logging

from core.models import IncidentState

logger = logging.getLogger(__name__)


async def handle_false_positive_side_effects(
    correlation_id: str,
    notes: str,
    incident: IncidentState,
) -> None:
    """Processa efeitos colaterais de um falso positivo.

    1. Tenta marcar padrão correspondente como negative_example
    2. Emite evento de log estruturado
    """
    pattern_id = None

    try:
        from server.app import knowledge_store as _ks
        if _ks is not None:
            pattern_id = _mark_knowledge_pattern_negative(
                incident, _ks, correlation_id,
            )
    except Exception:
        pass

    logger.info(
        "feedback.false_positive",
        extra={
            "correlation_id": correlation_id,
            "notes": notes,
            "pattern_id": pattern_id,
        },
    )


def _mark_knowledge_pattern_negative(
    incident: IncidentState,
    knowledge_store: object,
    correlation_id: str,
) -> str | None:
    """Marca padrão correspondente como negative_example na base de conhecimento.

    Busca por source_incident_id primeiro, depois por service+root_cause.
    Retorna pattern_id se encontrado, None caso contrário.
    """
    # Busca por correlation_id no source_incident_id de padrões
    pattern = None
    for p in knowledge_store.get_all_patterns():
        if getattr(p, "source_incident_id", None) == correlation_id:
            pattern = p
            break

    # Fallback: busca por service + root_cause a partir do diagnóstico
    if pattern is None and incident.diagnosis:
        service = incident.alert.affected_services[0] if incident.alert.affected_services else None
        root_cause = incident.diagnosis.get("root_cause")
        if service and root_cause:
            pattern = knowledge_store.find_by_service_and_cause(service, root_cause)

    if pattern is not None:
        knowledge_store.mark_pattern_negative(pattern.pattern_id)
        logger.info(
            "knowledge.pattern_marked_negative",
            extra={
                "pattern_id": pattern.pattern_id,
                "correlation_id": correlation_id,
            },
        )
        return pattern.pattern_id

    logger.info(
        "knowledge.no_matching_pattern",
        extra={"correlation_id": correlation_id},
    )
    return None
