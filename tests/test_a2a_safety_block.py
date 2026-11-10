"""A rota A2A informa um bloqueio de segurança, em vez de um erro genérico.

Pendência do P1 (docs/p1-progresso.md, item 5): com a safety ligada no V1,
um bloqueio levanta PipelineAborted, e a rota devolvia o mesmo
`pipeline_error` / "diagnosis failed" de uma falha qualquer. O cliente não
tinha como saber que o diagnóstico existia e que a AÇÃO foi bloqueada.
"""
from __future__ import annotations

import dataclasses
import secrets

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.jwt_validator import issue_token
from blueprint.adapters.safety_nodes import SafetyBlockedError
from blueprint.engine import PipelineAborted
from interfaces.http.routes.a2a import router as a2a_router

SECRET = secrets.token_urlsafe(48)


class _BlockingEngine:
    """Engine que reproduz o aborto real de um safety node."""

    def __init__(self, with_diagnosis: bool = True, cause: Exception | None = None):
        self.with_diagnosis = with_diagnosis
        self.cause = cause or SafetyBlockedError("PreExecutionValidator blocked: LOW")

    def run(self, pipeline, state):
        reached = state
        if self.with_diagnosis:
            reached = dataclasses.replace(
                state, diagnosis={"root_cause": "pool exhausted", "confidence": "high"})
        try:
            raise self.cause
        except Exception as exc:
            raise PipelineAborted(
                f"Node 'pre_validator' failed and on_failure=ABORT: {exc}",
                state=reached, node_id="pre_validator",
            ) from exc


def _post(engine) -> dict:
    app = FastAPI()
    app.include_router(a2a_router)
    app.state.a2a_jwt_secret = SECRET
    app.state.engine = engine
    token = issue_token("assistant-agent", ["aiops:diagnose"], SECRET)
    resp = TestClient(app).post("/a2a/tasks", json={
        "jsonrpc": "2.0", "id": "1", "method": "tasks/send",
        "params": {
            "id": "task-1",
            "skill": {"id": "diagnose_incident"},
            "message": {"role": "user", "parts": [{"type": "data", "data": {
                "source": "pagerduty", "severity": "high", "service": "api", "title": "t"}}]},
        },
    }, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.unit
class TestA2ASafetyBlock:
    def test_block_with_diagnosis_returns_diagnosis_and_safety_artifact(self):
        body = _post(_BlockingEngine())
        result = body["result"]
        assert result["status"]["state"] == "completed"
        artifacts = {a["name"]: a["parts"][0]["data"] for a in result["artifacts"]}
        assert artifacts["diagnosis"]["root_cause"] == "pool exhausted"
        safety = artifacts["safety"]
        assert safety["verdict"] == "BLOCKED"
        assert safety["blocked_by"] == "pre_validator"
        assert safety["requires_human"] is True
        assert "PreExecutionValidator blocked" in safety["reason"]

    def test_block_without_diagnosis_has_specific_error_code(self):
        body = _post(_BlockingEngine(with_diagnosis=False))
        assert body["error"]["data"]["error"] == "safety_blocked"

    def test_non_safety_abort_is_still_a_generic_pipeline_error(self):
        body = _post(_BlockingEngine(cause=RuntimeError("aggregator crashed")))
        assert body["error"]["data"]["error"] == "pipeline_error"
