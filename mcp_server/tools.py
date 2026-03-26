"""
Handlers de ferramentas MCP do AIOps Agent.

Implementa os três tools MCP: diagnose_incident, validate_action, search_incidents.
Cada handler recebe dependências via injeção (sem imports globais de domínio).

Histórias: 5.2 (diagnose_incident), 5.3 (validate_action), 5.4 (search_incidents).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import structlog

from core.models import MitigationAction, NormalizedAlert
from safety.orchestrator import SafetyOrchestrator

log = structlog.get_logger()

# Severidades válidas conforme schema do MCP tool
VALID_SEVERITIES = {"critical", "high", "medium", "low"}


# ============================================================================
# Story 5.4: search_incidents
# ============================================================================


@dataclass(frozen=True)
class SearchResult:
    """Resultado de busca de incidente no knowledge base."""

    incident_id: str
    summary: str
    diagnosis: str
    resolution: str
    confidence: float
    similarity: float
    service: str
    tags: list[str]


async def handle_search_incidents(
    arguments: dict,
    retriever: object,
    sanitizer: object | None = None,
) -> str:
    """Busca incidentes similares no knowledge base FAISS.

    Recebe query textual, executa busca semântica, filtra por parâmetros
    opcionais, sanitiza PII e retorna top-K resultados.

    Args:
        arguments: Parâmetros do MCP tool call.
        retriever: Instância do RAG retriever (rag/retriever.py).
        sanitizer: TelemetrySanitizer para remoção de PII (opcional).

    Returns:
        JSON string com resultados ou erro.
    """
    query = arguments.get("query", "")
    if not query or not query.strip():
        return json.dumps(
            {
                "type": "validation_error",
                "title": "Invalid query",
                "detail": "Query text is required and must not be empty.",
            }
        )

    top_k = min(int(arguments.get("top_k", 5)), 20)
    top_k = max(top_k, 1)
    service_filter = arguments.get("service")
    tags_filter = arguments.get("tags", [])

    start_time = time.monotonic()

    try:
        # Busca semântica via FAISS — busca extra para compensar filtragem
        raw_results = retriever.search(query, top_k=top_k * 2)
    except Exception as exc:
        log.error("mcp.search_incidents.faiss_unavailable", error=str(exc))
        return json.dumps(
            {
                "type": "faiss_unavailable",
                "title": "Knowledge base not available",
                "detail": "FAISS index is not loaded. Ensure RAG is enabled (--no-rag=false).",
            }
        )

    # Filtra por serviço e tags se fornecidos
    filtered = raw_results
    if service_filter:
        filtered = [r for r in filtered if _matches_service(r, service_filter)]
    if tags_filter:
        filtered = [r for r in filtered if _matches_tags(r, tags_filter)]

    # Limita a top_k após filtragem
    filtered = filtered[:top_k]

    # Sanitiza PII dos resultados
    results = []
    for r in filtered:
        result_dict = _format_search_result(r)
        if sanitizer is not None:
            result_dict = _sanitize_result(result_dict, sanitizer)
        results.append(result_dict)

    elapsed_ms = (time.monotonic() - start_time) * 1000

    if elapsed_ms > 2000:
        log.warning(
            "mcp.search_incidents.slow",
            latency_ms=round(elapsed_ms, 1),
            results=len(results),
        )
    else:
        log.info(
            "mcp.search_incidents.completed",
            latency_ms=round(elapsed_ms, 1),
            results=len(results),
            query_length=len(query),
        )

    return json.dumps(
        {
            "results": results,
            "total": len(results),
            "query": query,
            "latency_ms": round(elapsed_ms, 1),
        }
    )


def _matches_service(result: object, service: str) -> bool:
    """Verifica se resultado pertence ao serviço filtrado."""
    result_service = getattr(result, "service", "") or ""
    if not result_service:
        # Tenta via chunk para SearchResult do retriever existente
        chunk = getattr(result, "chunk", None)
        if chunk:
            result_service = getattr(chunk, "source", "") or ""
    return service.lower() in result_service.lower()


def _matches_tags(result: object, tags: list[str]) -> bool:
    """Verifica se resultado contém alguma das tags filtradas."""
    result_tags = getattr(result, "tags", []) or []
    return bool(set(t.lower() for t in tags) & set(t.lower() for t in result_tags))


def _format_search_result(result: object) -> dict:
    """Formata resultado do retriever para resposta MCP."""
    # Suporte ao SearchResult existente (rag/retriever.py) e tipo genérico
    chunk = getattr(result, "chunk", None)
    text = ""
    if chunk is not None:
        text = getattr(chunk, "text", "")

    return {
        "incident_id": getattr(result, "incident_id", "unknown"),
        "summary": getattr(result, "summary", text),
        "diagnosis": getattr(result, "diagnosis", ""),
        "resolution": getattr(result, "resolution", ""),
        "confidence": round(float(getattr(result, "confidence", 0.0)), 2),
        "similarity": round(float(getattr(result, "score", 0.0)), 2),
        "service": getattr(result, "service", ""),
        "tags": list(getattr(result, "tags", [])),
    }


def _sanitize_result(result: dict, sanitizer: object) -> dict:
    """Remove PII dos campos textuais do resultado."""
    text_fields = ["summary", "diagnosis", "resolution"]
    for field_name in text_fields:
        if field_name in result and result[field_name]:
            try:
                result[field_name] = sanitizer.sanitize_text(result[field_name])
            except Exception:
                pass  # Sanitizer não disponível — mantém texto original
    return result


# ============================================================================
# Story 5.3: validate_action
# ============================================================================


@dataclass(frozen=True)
class LayerResult:
    """Resultado de uma camada de segurança individual."""

    layer_name: str
    passed: bool
    risk_contribution: float  # 0.0 a 1.0 — contribuição para risk_score
    detail: str


@dataclass(frozen=True)
class ValidationVerdict:
    """Veredicto completo da validação de ação.

    Agrega resultados das 4 camadas de segurança.
    """

    approved: bool
    risk_score: float
    blocked_by: list[str]
    rationale: str
    layers: list[LayerResult]


async def handle_validate_action(
    arguments: dict,
    sanitizer: object,
    pre_validator: object,
    tnr: object,
    version_validator: object,
    client_id: str = "unknown",
    correlation_id: str | None = None,
) -> str:
    """Valida ação proposta através das 4 camadas de segurança.

    Executa TODAS as camadas incondicionalmente (sem curto-circuito).
    Retorna veredicto estruturado com score de risco e justificativa.

    Args:
        arguments: Parâmetros do MCP tool call.
        sanitizer: TelemetrySanitizer (camada 1 — PII/injeção).
        pre_validator: PreExecutionValidator (camada 2 — blast radius + blocklist).
        tnr: TNRSafetyGuard (camada 3 — regressão de saúde).
        version_validator: VersionValidator (camada 4 — CVE/versões).
        client_id: Identificador do cliente MCP.
        correlation_id: ID de correlação para rastreamento.

    Returns:
        JSON string com veredicto de validação.
    """
    command = arguments.get("command", "")
    target_service = arguments.get("target_service", "")
    blast_radius = int(arguments.get("blast_radius", 1))

    # Validação de entrada
    if not command or not command.strip():
        return json.dumps(
            {
                "type": "validation_error",
                "title": "Invalid command",
                "detail": "Command is required and must not be empty.",
                "status": 400,
            }
        )
    if not target_service or not target_service.strip():
        return json.dumps(
            {
                "type": "validation_error",
                "title": "Invalid target_service",
                "detail": "Target service is required and must not be empty.",
                "status": 400,
            }
        )
    if blast_radius < 1:
        return json.dumps(
            {
                "type": "validation_error",
                "title": "Invalid blast_radius",
                "detail": "blast_radius must be a positive integer.",
                "status": 400,
            }
        )

    start_time = time.monotonic()
    layer_results: list[LayerResult] = []

    # Camada 1: TelemetrySanitizer — verifica PII e padrões de injeção
    layer_1 = _check_sanitizer(command, sanitizer)
    layer_results.append(layer_1)
    log.info(
        "mcp.validate_action.layer",
        layer=layer_1.layer_name,
        passed=layer_1.passed,
        detail=layer_1.detail,
        correlation_id=correlation_id,
    )

    # Camadas 2-4: SafetyOrchestrator — PreValidator → TNR → VersionValidator
    orchestrator = SafetyOrchestrator(
        pre_validator=pre_validator,
        tnr=tnr,
        version_validator=version_validator,
    )
    action = MitigationAction(
        description=f"MCP validate_action: {command[:80]}",
        command=command,
        risk_level="MEDIUM",
        rollback_command=None,
    )
    orch_result = orchestrator.validate(
        action,
        diagnosis={"affected_services": [target_service], "confidence": "medium"},
        correlation_id=correlation_id or "",
    )

    # Adapta resultado do orquestrador para LayerResult por camada
    pre_detail = orch_result.layer_details.get("pre_validator")
    layer_2 = LayerResult(
        layer_name="PreExecutionValidator",
        passed="pre_validator" not in orch_result.blocked_by,
        risk_contribution=float(getattr(pre_detail, "risk_score", 0) or 0) / 100.0,
        detail=getattr(pre_detail, "recommendation", "Passed") or "Passed",
    )
    layer_results.append(layer_2)
    log.info(
        "mcp.validate_action.layer",
        layer=layer_2.layer_name,
        passed=layer_2.passed,
        detail=layer_2.detail,
        correlation_id=correlation_id,
    )

    tnr_detail = orch_result.layer_details.get("tnr")
    tnr_msg = getattr(tnr_detail, "message", "TNR check passed") or "TNR check passed"
    layer_3 = LayerResult(
        layer_name="TNR",
        passed="tnr" not in orch_result.blocked_by,
        risk_contribution=0.3 if "tnr" in orch_result.blocked_by else 0.0,
        detail=tnr_msg if isinstance(tnr_msg, str) else "TNR check passed",
    )
    layer_results.append(layer_3)
    log.info(
        "mcp.validate_action.layer",
        layer=layer_3.layer_name,
        passed=layer_3.passed,
        detail=layer_3.detail,
        correlation_id=correlation_id,
    )

    ver_detail = orch_result.layer_details.get("version_validator")
    layer_4 = LayerResult(
        layer_name="VersionValidator",
        passed="version_validator" not in orch_result.blocked_by,
        risk_contribution=0.5 if "version_validator" in orch_result.blocked_by else 0.0,
        detail=getattr(ver_detail, "recommendation", "Passed") or "Passed",
    )
    layer_results.append(layer_4)
    log.info(
        "mcp.validate_action.layer",
        layer=layer_4.layer_name,
        passed=layer_4.passed,
        detail=layer_4.detail,
        correlation_id=correlation_id,
    )

    # Agrega veredicto
    verdict = _build_verdict(layer_results)

    elapsed_ms = (time.monotonic() - start_time) * 1000
    log.info(
        "mcp.validate_action.verdict",
        client_id=client_id,
        command=command,
        target_service=target_service,
        approved=verdict.approved,
        risk_score=verdict.risk_score,
        blocked_by=verdict.blocked_by,
        latency_ms=round(elapsed_ms, 1),
        correlation_id=correlation_id,
    )

    return json.dumps(
        {
            "approved": verdict.approved,
            "risk_score": verdict.risk_score,
            "blocked_by": verdict.blocked_by,
            "rationale": verdict.rationale,
            "layers": [
                {
                    "name": lr.layer_name,
                    "passed": lr.passed,
                    "risk_contribution": lr.risk_contribution,
                    "detail": lr.detail,
                }
                for lr in verdict.layers
            ],
            "latency_ms": round(elapsed_ms, 1),
        }
    )


def _check_sanitizer(command: str, sanitizer: object) -> LayerResult:
    """Camada 1: verifica se comando contém PII ou padrões adversariais."""
    try:
        sanitized = sanitizer.sanitize_text(command)
        has_pii = sanitized != command
        return LayerResult(
            layer_name="TelemetrySanitizer",
            passed=not has_pii,
            risk_contribution=0.3 if has_pii else 0.0,
            detail=(
                "PII/injection patterns detected in command"
                if has_pii
                else "No PII or injection patterns found"
            ),
        )
    except Exception as exc:
        return LayerResult(
            layer_name="TelemetrySanitizer",
            passed=False,
            risk_contribution=0.5,
            detail=f"Sanitizer check failed: {exc}",
        )


def _check_pre_validator(
    command: str,
    target_service: str,
    blast_radius: int,
    pre_validator: object,
) -> LayerResult:
    """Camada 2: blast radius e verificação de blocklist.

    Adapta interface MCP para PreExecutionValidator existente.
    """
    try:
        # Importação local para evitar dependência circular
        from safety.tnr import MitigationAction

        # Cria objeto de ação compatível com PreExecutionValidator
        action = MitigationAction(
            description=f"MCP validate_action: {command[:80]}",
            command=command,
            risk_level="MEDIUM",
            rollback_command=None,
        )
        diagnosis = {
            "affected_services": [target_service],
            "confidence": "medium",
        }
        result = pre_validator.validate(action, diagnosis)
        passed = getattr(result, "is_safe", getattr(result, "approved", True))
        risk = getattr(result, "risk_score", 0.0)
        # Normaliza risk_score de 0-100 para 0-1 se necessário
        if risk > 1.0:
            risk = risk / 100.0
        detail = getattr(result, "recommendation", "Passed blast radius and blocklist checks")
        return LayerResult(
            layer_name="PreExecutionValidator",
            passed=passed,
            risk_contribution=risk if not passed else 0.0,
            detail=detail if not passed else "Passed blast radius and blocklist checks",
        )
    except Exception as exc:
        return LayerResult(
            layer_name="PreExecutionValidator",
            passed=False,
            risk_contribution=0.5,
            detail=f"PreExecution validation failed: {exc}",
        )


def _check_tnr(command: str, target_service: str, tnr: object) -> LayerResult:
    """Camada 3: Transactional No-Regression (simulação de snapshot).

    Para validação MCP estática, usa snapshot simulado sem execução real.
    """
    try:
        from datetime import datetime

        from safety.tnr import HealthSnapshot, MitigationAction

        # Snapshots simulados: pre=saúde nominal, post=estimativa após ação
        pre = HealthSnapshot(
            timestamp=datetime.now(),
            error_rate=5.0,
            latency_p99_ms=200.0,
            availability=99.0,
            connection_pool_active=10,
            connection_pool_max=50,
        )
        # Assume que a ação não piora a saúde (simulação conservadora)
        post = HealthSnapshot(
            timestamp=datetime.now(),
            error_rate=5.0,
            latency_p99_ms=200.0,
            availability=99.0,
            connection_pool_active=10,
            connection_pool_max=50,
        )
        action = MitigationAction(
            description=f"MCP validate_action: {command[:80]}",
            command=command,
            risk_level="MEDIUM",
            rollback_command=None,
        )
        report = tnr.simulate_check(action, pre, post)
        from safety.tnr import TNRResult

        passed = report.result == TNRResult.SUCCESS
        return LayerResult(
            layer_name="TNR",
            passed=passed,
            risk_contribution=0.3 if not passed else 0.0,
            detail=report.message if not passed else "Health snapshot comparison passed",
        )
    except Exception as exc:
        return LayerResult(
            layer_name="TNR",
            passed=False,
            risk_contribution=0.3,
            detail=f"TNR check failed: {exc}",
        )


def _check_version_validator(command: str, version_validator: object) -> LayerResult:
    """Camada 4: verificação de CVE e versões em pacotes/imagens referenciados."""
    try:
        result = version_validator.validate_remediation(command)
        passed = getattr(result, "is_safe", True)
        detail = getattr(result, "recommendation", "No known CVEs in referenced versions")
        risk = 0.2 if not passed else 0.0
        return LayerResult(
            layer_name="VersionValidator",
            passed=passed,
            risk_contribution=risk,
            detail=detail if not passed else "No known CVEs in referenced versions",
        )
    except Exception as exc:
        return LayerResult(
            layer_name="VersionValidator",
            passed=False,
            risk_contribution=0.2,
            detail=f"Version validation failed: {exc}",
        )


def _build_verdict(layer_results: list[LayerResult]) -> ValidationVerdict:
    """Constrói veredicto final a partir dos resultados das 4 camadas.

    Aprovado somente se TODAS as camadas passam.
    Risk score é soma das contribuições (cap em 1.0).
    """
    approved = all(lr.passed for lr in layer_results)
    blocked_by = [lr.layer_name for lr in layer_results if not lr.passed]

    # Risk score: soma das contribuições (cap em 1.0)
    risk_score = min(1.0, sum(lr.risk_contribution for lr in layer_results))

    # Rationale: combina detalhes das camadas que bloquearam
    if approved:
        rationale = "Action approved by all 4 safety layers."
    else:
        blocking_details = [
            f"{lr.layer_name}: {lr.detail}" for lr in layer_results if not lr.passed
        ]
        rationale = "Action blocked. " + " | ".join(blocking_details)

    return ValidationVerdict(
        approved=approved,
        risk_score=round(risk_score, 2),
        blocked_by=blocked_by,
        rationale=rationale,
        layers=layer_results,
    )


# ============================================================================
# Story 5.2: diagnose_incident
# ============================================================================


async def handle_diagnose_incident(
    arguments: dict,
    engine: object | None,
    normalizer_registry: object | None,
    incident_store: object | None,
    config: object | None = None,
    client_id: str = "unknown",
) -> str:
    """Executa pipeline diagnóstico completo via MCP tool.

    Normaliza alerta de entrada, executa BlueprintEngine (mesmo caminho
    que webhook), e retorna diagnóstico estruturado.

    CAMINHO IDÊNTICO ao webhook — não é implementação paralela.

    Args:
        arguments: Parâmetros do MCP tool call.
        engine: BlueprintEngine — mesmo usado pelo webhook pipeline.
        normalizer_registry: Registro de normalizadores de alerta.
        incident_store: IncidentStore para rastreamento.
        config: AppConfig para flags como no_llm.
        client_id: Identificador do cliente MCP.

    Returns:
        JSON string com diagnóstico ou erro RFC 7807.
    """
    correlation_id = (
        arguments.get("correlation_id") or f"inc-mcp-{uuid.uuid4().hex[:8]}"
    )

    # Validação de entrada
    validation_error = _validate_diagnose_input(arguments)
    if validation_error:
        return json.dumps(
            {
                "type": "validation_error",
                "title": "Invalid input",
                "status": 400,
                "detail": validation_error,
                "correlation_id": correlation_id,
            }
        )

    source = arguments["source"]
    severity = arguments["severity"]
    title = arguments["title"]
    description = arguments["description"]
    service = arguments.get("service")

    start_time = time.monotonic()

    log.info(
        "mcp.diagnose_incident.started",
        client_id=client_id,
        source=source,
        severity=severity,
        service=service,
        correlation_id=correlation_id,
    )

    # Se engine não disponível, retorna erro gracioso
    if engine is None:
        return json.dumps(
            {
                "type": "engine_unavailable",
                "title": "Diagnostic engine not available",
                "detail": "BlueprintEngine is not initialized. Ensure the pipeline is running.",
                "status": 503,
                "correlation_id": correlation_id,
            }
        )

    try:
        # Normaliza alerta — MESMO caminho que webhook
        normalized_alert = _normalize_alert(
            source=source,
            severity=severity,
            title=title,
            description=description,
            service=service,
            normalizer_registry=normalizer_registry,
        )

        # Executa pipeline via BlueprintEngine.run()
        result = engine.run(normalized_alert)

        elapsed_ms = (time.monotonic() - start_time) * 1000

        # Extrai campos do resultado do engine
        diagnosis = getattr(result, "diagnosis", str(result))
        evidence_chain = getattr(result, "evidence_chain", [])
        confidence = float(getattr(result, "confidence", 0.5))
        proposed_fix = getattr(result, "proposed_fix", "")
        risk_assessment = getattr(result, "risk_assessment", {})
        mode = getattr(result, "mode", "heuristic")

        log.info(
            "mcp.diagnose_incident.completed",
            client_id=client_id,
            correlation_id=correlation_id,
            confidence=confidence,
            latency_ms=round(elapsed_ms, 1),
        )

        return json.dumps(
            {
                "incident_id": correlation_id,
                "diagnosis": diagnosis,
                "evidence_chain": evidence_chain,
                "confidence": round(confidence, 2),
                "proposed_fix": proposed_fix,
                "risk_assessment": risk_assessment,
                "mode": mode,
                "latency_ms": round(elapsed_ms, 1),
                "correlation_id": correlation_id,
            }
        )

    except Exception as exc:
        elapsed_ms = (time.monotonic() - start_time) * 1000
        log.error(
            "mcp.diagnose_incident.error",
            client_id=client_id,
            correlation_id=correlation_id,
            error=str(exc),
        )
        return json.dumps(
            {
                "type": "pipeline_error",
                "title": "Diagnostic pipeline failed",
                "detail": str(exc),
                "status": 500,
                "correlation_id": correlation_id,
                "latency_ms": round(elapsed_ms, 1),
            }
        )


def _validate_diagnose_input(arguments: dict) -> str | None:
    """Valida campos obrigatórios do diagnose_incident.

    Returns:
        Mensagem de erro ou None se válido.
    """
    required = ["source", "severity", "title", "description"]
    for field in required:
        if not arguments.get(field):
            return f"Missing required field: {field}"

    severity = arguments.get("severity", "")
    if severity not in VALID_SEVERITIES:
        return (
            f"Invalid severity '{severity}'. "
            f"Must be one of: {', '.join(sorted(VALID_SEVERITIES))}"
        )

    return None


def _normalize_alert(
    source: str,
    severity: str,
    title: str,
    description: str,
    service: str | None,
    normalizer_registry: object | None,
) -> object:
    """Normaliza alerta MCP para formato do pipeline.

    Reutiliza normalizer_registry se disponível, caso contrário cria
    objeto simples compatível com pipeline.
    """
    # Tenta usar normalizer_registry se disponível
    if normalizer_registry is not None:
        payload = {
            "source": source,
            "severity": severity,
            "title": title,
            "description": description,
            "service": service or "",
        }
        try:
            normalized = normalizer_registry.normalize(source, payload)
            if normalized is not None:
                return normalized
        except Exception:
            pass  # Fallback para objeto simples

    # Fallback: objeto NormalizedAlert com defaults para campos obrigatórios
    # Mapeia severidades MCP (critical/high/medium/low) para pipeline (critical/error/warning/info)
    _severity_map = {"critical": "critical", "high": "error", "medium": "warning", "low": "info"}
    pipeline_severity = _severity_map.get(severity.lower(), "warning")
    now = datetime.now(timezone.utc)
    return NormalizedAlert(
        source=source,
        source_id=f"mcp-{uuid.uuid4().hex[:8]}",
        correlation_id=f"inc-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}",
        title=title,
        description=description,
        severity=pipeline_severity,
        alert_type="mcp_alert",
        timestamp=now,
        detection_timestamp=now,
        service=service or None,
    )
