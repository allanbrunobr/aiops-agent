"""Integração A2A (Agent-to-Agent) para o AIOps Agent.

Implementa o protocolo A2A v0.3 (Linux Foundation):
  GET  /a2a/agent-card.json — Agent Card (discovery)
  POST /a2a/tasks           — recebe tasks JSON-RPC 2.0

O handler traduz tasks A2A para o pipeline existente (BlueprintEngine)
sem alterar nenhuma lógica de negócio do core.

Baseado em: A2A Protocol v0.3 + IETF draft-klrc-aiagent-auth-00.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import jwt as pyjwt
import structlog
from fastapi import APIRouter, HTTPException, Request

from auth.jwt_validator import MIN_HMAC_KEY_BYTES, validate_token

log = structlog.get_logger()

router = APIRouter(prefix="/a2a", tags=["a2a"])

# Same ceiling as the webhook receiver.
_MAX_BODY_BYTES = 1_048_576

_SKILL_SCOPES = {
    "diagnose_incident": "aiops:diagnose",
    "search_incidents": "aiops:search",
}

# Agent Card estático — descrevendo as skills do AIOps Agent
AGENT_CARD: dict[str, Any] = {
    "name": "AIOps Agent",
    "description": "Detects, diagnoses and proposes mitigations for infrastructure incidents",
    "version": "1.0.0",
    "url": "/a2a/tasks",
    "skills": [
        {
            "id": "diagnose_incident",
            "name": "Diagnose Incident",
            "description": (
                "Executa o pipeline ReAct completo sobre logs/métricas/traces. "
                "Retorna diagnosis + mitigation_proposal + postmortem."
            ),
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
        {
            "id": "search_incidents",
            "name": "Search Past Incidents",
            "description": "Busca semântica (RAG) sobre histórico de incidentes.",
            "inputModes": ["application/json"],
            "outputModes": ["application/json"],
        },
    ],
    "securitySchemes": {
        "oauth2": {
            "type": "oauth2",
            "flows": {
                "clientCredentials": {
                    "tokenUrl": "/auth/token/register",
                    "scopes": {
                        "aiops:diagnose": "Run full diagnosis pipeline",
                        "aiops:search": "Search incident history (RAG)",
                        "aiops:validate": "Validate remediation actions",
                    },
                }
            },
        }
    },
}


@router.get("/agent-card.json")
async def agent_card() -> dict:
    """Retorna o Agent Card do AIOps Agent (discovery A2A)."""
    return AGENT_CARD


def _configured_hmac_secret(request: Request) -> str:
    """Secret HS256 usado pelo mesmo validador do MCP OAuth.

    A fonte é app.state.a2a_jwt_secret, que create_app copia de
    mcp.oauth.jwt_secret. Sem chave de 32 bytes a rota não aceita ninguem.
    """
    secret = getattr(request.app.state, "a2a_jwt_secret", "") or ""
    if len(secret.encode("utf-8")) < MIN_HMAC_KEY_BYTES:
        return ""
    return secret


def _require_bearer(request: Request) -> Any:
    """Valida Authorization: Bearer com o mesmo HS256 do MCP."""
    secret = _configured_hmac_secret(request)
    if not secret:
        raise HTTPException(
            status_code=401,
            detail="A2A authentication is not configured",
        )
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer ") or not header[7:].strip():
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        return validate_token(header[7:].strip(), secret)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="JWT has expired")
    except pyjwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="JWT is invalid")


@router.post("/tasks")
async def handle_task(request: Request) -> dict:
    """Recebe e processa tasks A2A no formato JSON-RPC 2.0.

    Exige Bearer HS256. Falha de skill volta erro JSON-RPC, não
    status.state=completed.
    """
    claims = _require_bearer(request)

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared = int(content_length)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length")
        if declared > _MAX_BODY_BYTES:
            raise HTTPException(status_code=413, detail="Payload too large")

    raw = await request.body()
    if len(raw) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Payload too large")

    try:
        body = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON body must be an object")

    rpc_id = body.get("id", "1")
    params = body.get("params", {})
    if not isinstance(params, dict):
        params = {}
    task_id = params.get("id", str(uuid.uuid4()))
    skill = params.get("skill", {})
    skill_id = skill.get("id") if isinstance(skill, dict) else None
    message = params.get("message", {})
    parts = message.get("parts", []) if isinstance(message, dict) else []
    data = parts[0].get("data", {}) if parts and isinstance(parts[0], dict) else {}
    if not isinstance(data, dict):
        data = {}

    log.info("a2a.task.received", task_id=task_id, skill_id=skill_id)

    required_scope = _SKILL_SCOPES.get(skill_id or "")
    if required_scope is None:
        return _rpc_error(
            rpc_id=rpc_id,
            task_id=task_id,
            code=-32601,
            message=f"Unknown skill: '{skill_id}'",
        )

    if required_scope not in claims.scopes:
        return _rpc_error(
            rpc_id=rpc_id,
            task_id=task_id,
            code=-32003,
            message=f"Insufficient scope: {required_scope} required",
        )

    if skill_id == "diagnose_incident":
        artifacts = await _handle_diagnose(task_id=task_id, data=data, request=request)
    else:
        artifacts = await _handle_search(task_id=task_id, data=data, request=request)

    failure = _skill_failure(artifacts)
    if failure is not None:
        log.info("a2a.task.failed", task_id=task_id, skill_id=skill_id, error=failure)
        return _rpc_error(
            rpc_id=rpc_id,
            task_id=task_id,
            code=-32603,
            message="Skill execution failed",
            error_code=failure,
        )

    response = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {
            "id": task_id,
            "status": {"state": "completed"},
            "artifacts": artifacts,
        },
    }
    log.info("a2a.task.completed", task_id=task_id, skill_id=skill_id)
    return response


# ---------------------------------------------------------------------------
# Handlers de skill — traduzem A2A → pipeline existente
# ---------------------------------------------------------------------------

def _map_severity(raw: str) -> str:
    """Normaliza severity A2A → NormalizedAlert (critical/error/warning/info).

    Levanta ValueError se o valor mapeado não pertencer ao vocabulário canônico —
    handler externo captura e devolve artifact ``pipeline_error``.
    """
    from interfaces.http.webhook.schemas import normalize_severity
    _MAP = {"high": "error", "medium": "warning", "low": "info", "p1": "critical", "p2": "error"}
    normalized = raw.lower()
    mapped = _MAP.get(normalized, normalized)
    return normalize_severity(mapped)


async def _handle_diagnose(task_id: str, data: dict, request: Request) -> list[dict]:
    """Traduz task A2A diagnose_incident → BlueprintEngine."""
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return [_error_artifact("engine_unavailable", "BlueprintEngine not initialized")]

    try:
        from datetime import datetime, timezone
        from domain.alert import NormalizedAlert
        from domain.incident_state import IncidentState

        now = datetime.now(timezone.utc)
        alert = NormalizedAlert(
            source=data.get("source", "a2a"),
            source_id=data.get("source_id", task_id),
            correlation_id=task_id,
            title=data.get("title", "Incident via A2A"),
            description=data.get("description", ""),
            severity=_map_severity(data.get("severity", "error")),
            alert_type=data.get("alert_type", "A2ATask"),
            timestamp=data.get("timestamp", now),
            detection_timestamp=data.get("detection_timestamp", now),
            service=data.get("service"),
        )
        state = IncidentState(
            incident_id=task_id,
            correlation_id=task_id,
            alert=alert,
            received_at=now,
            started_at=alert.timestamp,
        )

        from app.builders import build_pipeline
        config = getattr(request.app.state, "config", None)
        pipeline = build_pipeline(config)
        result = engine.run(pipeline, state)

        return [
            {
                "name": "diagnosis",
                "parts": [{"type": "data", "data": _safe_dict(result, "diagnosis")}],
            },
            {
                "name": "mitigation",
                "parts": [{"type": "data", "data": _safe_dict(result, "mitigation_proposal")}],
            },
        ]
    except Exception as exc:
        blocked = _safety_block_artifacts(exc)
        if blocked is not None:
            log.warning("a2a.diagnose.safety_blocked", task_id=task_id,
                        blocked_by=getattr(exc, "node_id", None), reason=str(exc.__cause__))
            return blocked
        log.error("a2a.diagnose.error", task_id=task_id, error=str(exc))
        return [_error_artifact("pipeline_error", "diagnosis failed")]


def _safety_block_artifacts(exc: Exception) -> list[dict] | None:
    """Artifacts para um aborto causado por uma camada de safety; None se não for.

    Com o diagnóstico já feito, a task se completa com o diagnóstico e um
    artifact `safety` dizendo que a AÇÃO foi bloqueada e exige humano. Sem
    diagnóstico, um erro com código próprio (`safety_blocked`), distinto de
    uma falha do pipeline.
    """
    from blueprint.adapters.safety_nodes import SafetyBlockedError
    from blueprint.engine import PipelineAborted

    if not (isinstance(exc, PipelineAborted) and isinstance(exc.__cause__, SafetyBlockedError)):
        return None
    blocked_by = getattr(exc, "node_id", None) or "safety"
    reason = str(exc.__cause__)
    state = getattr(exc, "state", None)
    diagnosis = getattr(state, "diagnosis", None)
    if not diagnosis:
        return [_error_artifact("safety_blocked", f"{blocked_by}: {reason}")]
    return [
        {"name": "diagnosis", "parts": [{"type": "data", "data": diagnosis}]},
        {"name": "safety", "parts": [{"type": "data", "data": {
            "verdict": "BLOCKED",
            "blocked_by": blocked_by,
            "reason": reason,
            "requires_human": True,
        }}]},
    ]


async def _handle_search(task_id: str, data: dict, request: Request) -> list[dict]:
    """Traduz task A2A search_incidents → RAG retriever."""
    retriever = getattr(request.app.state, "retriever", None)
    if retriever is None:
        return [_error_artifact("retriever_unavailable", "RAG retriever not initialized")]

    try:
        query = data.get("query", "")
        try:
            top_k = int(data.get("top_k", 5))
        except (TypeError, ValueError):
            return [_error_artifact("invalid_top_k", "top_k must be an integer")]
        top_k = min(max(top_k, 1), 20)
        results = await retriever.retrieve(query=query, top_k=top_k)
        return [
            {
                "name": "search_results",
                "parts": [{"type": "data", "data": {"results": results, "query": query}}],
            }
        ]
    except Exception as exc:
        log.error("a2a.search.error", task_id=task_id, error=str(exc))
        return [_error_artifact("search_error", "search failed")]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_dict(obj: Any, attr: str) -> dict:
    """Extrai atributo como dict, retornando {} se ausente."""
    val = getattr(obj, attr, None)
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    if hasattr(val, "__dict__"):
        return val.__dict__
    return {"value": str(val)}


def _error_artifact(error_type: str, detail: str) -> dict:
    return {
        "name": "error",
        "parts": [{"type": "data", "data": {"error": error_type, "detail": detail}}],
    }


def _skill_failure(artifacts: list[dict]) -> str | None:
    """Código estável se o handler devolveu um artifact de erro."""
    if len(artifacts) == 1 and artifacts[0].get("name") == "error":
        parts = artifacts[0].get("parts") or []
        data = parts[0].get("data", {}) if parts else {}
        return str(data.get("error") or "skill_failed")
    return None


def _rpc_error(
    rpc_id: str,
    task_id: str,
    code: int,
    message: str,
    error_code: str | None = None,
) -> dict:
    data: dict[str, str] = {"task_id": str(task_id)}
    if error_code:
        data["error"] = error_code
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": code,
            "message": message,
            "data": data,
        },
    }
