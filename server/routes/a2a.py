"""Integração A2A (Agent-to-Agent) para o AIOps Agent.

Implementa o protocolo A2A v0.3 (Linux Foundation):
  GET  /a2a/agent-card.json — Agent Card (discovery)
  POST /a2a/tasks           — recebe tasks JSON-RPC 2.0

O handler traduz tasks A2A para o pipeline existente (BlueprintEngine)
sem alterar nenhuma lógica de negócio do core.

Baseado em: A2A Protocol v0.3 + IETF draft-klrc-aiagent-auth-00.
"""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request

log = structlog.get_logger()

router = APIRouter(prefix="/a2a", tags=["a2a"])

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


@router.post("/tasks")
async def handle_task(request: Request) -> dict:
    """Recebe e processa tasks A2A no formato JSON-RPC 2.0.

    Suporta skills:
      - diagnose_incident: roda o pipeline BlueprintEngine
      - search_incidents: busca RAG sobre histórico

    O estado A2A retornado sempre é "completed" (execução síncrona).
    Para execução assíncrona com streaming, conectar via SSE.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Extrai campos JSON-RPC 2.0
    rpc_id = body.get("id", "1")
    params = body.get("params", {})
    task_id = params.get("id", str(uuid.uuid4()))
    skill = params.get("skill", {})
    skill_id = skill.get("id") if isinstance(skill, dict) else None
    message = params.get("message", {})
    parts = message.get("parts", []) if isinstance(message, dict) else []
    data = parts[0].get("data", {}) if parts and isinstance(parts[0], dict) else {}

    log.info("a2a.task.received", task_id=task_id, skill_id=skill_id)

    # Despacha para o handler da skill
    if skill_id == "diagnose_incident":
        artifacts = await _handle_diagnose(task_id=task_id, data=data, request=request)
    elif skill_id == "search_incidents":
        artifacts = await _handle_search(task_id=task_id, data=data, request=request)
    else:
        return _rpc_error(
            rpc_id=rpc_id,
            task_id=task_id,
            code=-32601,
            message=f"Unknown skill: '{skill_id}'",
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

async def _handle_diagnose(task_id: str, data: dict, request: Request) -> list[dict]:
    """Traduz task A2A diagnose_incident → BlueprintEngine."""
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return [_error_artifact("engine_unavailable", "BlueprintEngine not initialized")]

    try:
        from core.models import IncidentState, NormalizedAlert

        alert = NormalizedAlert(
            source=data.get("source", "a2a"),
            severity=data.get("severity", "high"),
            title=data.get("title", ""),
            description=data.get("description", ""),
            service=data.get("service"),
            correlation_id=task_id,
        )
        state = IncidentState(incident_id=task_id, alert=alert)

        from core.lifecycle import build_incident_response_pipeline
        config = getattr(request.app.state, "config", None)
        pipeline = build_incident_response_pipeline(config)
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
        log.error("a2a.diagnose.error", task_id=task_id, error=str(exc))
        return [_error_artifact("pipeline_error", str(exc))]


async def _handle_search(task_id: str, data: dict, request: Request) -> list[dict]:
    """Traduz task A2A search_incidents → RAG retriever."""
    retriever = getattr(request.app.state, "retriever", None)
    if retriever is None:
        return [_error_artifact("retriever_unavailable", "RAG retriever not initialized")]

    try:
        query = data.get("query", "")
        top_k = int(data.get("top_k", 5))
        results = await retriever.retrieve(query=query, top_k=top_k)
        return [
            {
                "name": "search_results",
                "parts": [{"type": "data", "data": {"results": results, "query": query}}],
            }
        ]
    except Exception as exc:
        log.error("a2a.search.error", task_id=task_id, error=str(exc))
        return [_error_artifact("search_error", str(exc))]


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


def _rpc_error(rpc_id: str, task_id: str, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "error": {
            "code": code,
            "message": message,
            "data": {"task_id": task_id},
        },
    }
