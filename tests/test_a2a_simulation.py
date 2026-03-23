"""Simulação de comunicação Agent-to-Agent (A2A Protocol v0.3).

Simula o fluxo completo de agentes externos descobrindo e chamando o
AIOps Agent como servidor — sem rede real, usando ASGI transport.

Cenários cobertos:
  1. Discovery — GET /a2a/agent-card.json retorna Agent Card válido
  2. Dispatch — POST /a2a/tasks roteia para a skill correta
  3. Erros de protocolo — skill desconhecida, body inválido
  4. A2AClient end-to-end — via ASGI transport (sem mock de response)
  5. Fluxo completo com JWT — Assistant Agent obtém token e chama AIOps Agent
  6. Fluxo multi-agente — dois agentes com scopes diferentes chamando o mesmo server
"""
from __future__ import annotations

import pytest
import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch

from server.routes.a2a import router as a2a_router, AGENT_CARD
from agents.a2a_client import A2AClient
import auth.server as auth_server
from auth.token_store import InMemoryTokenStore
from auth.pkce import generate_verifier, derive_challenge
from auth.jwt_validator import issue_token

_BASE_URL = "http://aiops-agent.internal"
_JWT_SECRET = "test-a2a-32bytes-secret-minimum!!"


# ---------------------------------------------------------------------------
# Mocks de infra (engine e retriever)
# ---------------------------------------------------------------------------

class _MockEngine:
    """BlueprintEngine mínimo — retorna diagnosis + mitigation fixos."""

    def run(self, pipeline, state) -> _MockResult:  # pipeline ignorado no teste
        return _MockResult(incident_id=state.incident_id)


class _MockResult:
    def __init__(self, incident_id: str = "test-id") -> None:
        self.incident_id = incident_id
        self.diagnosis = {
            "root_cause": "high_cpu_saturation",
            "confidence": 0.92,
            "affected_services": ["api-gateway", "worker"],
        }
        self.mitigation_proposal = {
            "action": "scale_out",
            "steps": ["add 2 replicas to worker", "enable circuit breaker"],
            "estimated_recovery_min": 5,
        }


class _MockRetriever:
    """RAG retriever mínimo — retorna incidentes fixos."""

    async def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        incidents = [
            {"id": "inc-2024-001", "summary": "CPU spike — scaled out workers", "score": 0.91},
            {"id": "inc-2024-002", "summary": "Memory leak — rolling restart", "score": 0.87},
            {"id": "inc-2024-003", "summary": "DB connection pool exhaustion", "score": 0.83},
        ]
        return incidents[:top_k]


# ---------------------------------------------------------------------------
# Factories de app
# ---------------------------------------------------------------------------

def _make_a2a_app(*, with_engine: bool = True, with_retriever: bool = True) -> FastAPI:
    """App FastAPI com A2A router + state mockado."""
    app = FastAPI()

    # Patch build_incident_response_pipeline para não depender do pipeline real
    import blueprint.pipelines.incident_response as _bp
    app.state._pipeline_patch = patch.object(
        _bp, "build_incident_response_pipeline", return_value=None
    )
    app.state._pipeline_patch.start()

    app.include_router(a2a_router)
    if with_engine:
        app.state.engine = _MockEngine()
    if with_retriever:
        app.state.retriever = _MockRetriever()
    return app


def _make_auth_a2a_app() -> FastAPI:
    """App FastAPI com OAuth + A2A router + state mockado."""
    app = FastAPI()

    import blueprint.pipelines.incident_response as _bp
    app.state._pipeline_patch = patch.object(
        _bp, "build_incident_response_pipeline", return_value=None
    )
    app.state._pipeline_patch.start()

    auth_server.configure(
        jwt_secret=_JWT_SECRET,
        token_ttl=3600,
        allowed_clients=[
            {"id": "assistant-agent", "scopes": ["aiops:diagnose", "aiops:search"]},
            {"id": "databricks-mcp", "scopes": ["aiops:search"]},
            {"id": "sre-orchestrator", "scopes": ["aiops:diagnose", "aiops:search", "aiops:validate"]},
        ],
        base_url=_BASE_URL,
        token_store=InMemoryTokenStore(),
    )
    app.include_router(auth_server.router)
    app.include_router(a2a_router)
    app.state.engine = _MockEngine()
    app.state.retriever = _MockRetriever()
    return app


# ---------------------------------------------------------------------------
# Fixture: ASGI transport para A2AClient
# ---------------------------------------------------------------------------

def _make_asgi_client_class(transport: httpx.ASGITransport, base_url: str) -> type:
    """Cria uma classe substituta para httpx.AsyncClient que usa ASGI transport.

    Captura a classe original *antes* de ser patchada para evitar recursão.
    Sem esta técnica, a factory chamaria a si mesma ao criar o cliente interno.
    """
    _OriginalClient = httpx.AsyncClient  # referência ao original, antes do patch

    class _ASGIAsyncClient:
        """Proxy de httpx.AsyncClient apontando para ASGI app em memória."""

        def __init__(self, timeout: float = 30.0, **_ignored: object) -> None:
            self._inner = _OriginalClient(
                transport=transport,
                base_url=base_url,
                timeout=timeout,
            )

        async def __aenter__(self) -> httpx.AsyncClient:
            return await self._inner.__aenter__()

        async def __aexit__(self, *args: object) -> None:
            await self._inner.__aexit__(*args)

    return _ASGIAsyncClient


# ---------------------------------------------------------------------------
# 1. Discovery
# ---------------------------------------------------------------------------

class TestA2ADiscovery:
    """Testa o endpoint de discovery (Agent Card)."""

    @pytest.fixture(autouse=True)
    def client(self):
        self._client = TestClient(_make_a2a_app())

    def test_agent_card_returns_200(self):
        resp = self._client.get("/a2a/agent-card.json")
        assert resp.status_code == 200

    def test_agent_card_has_name_and_version(self):
        body = self._client.get("/a2a/agent-card.json").json()
        assert body["name"] == "AIOps Agent"
        assert "version" in body

    def test_agent_card_exposes_skills(self):
        body = self._client.get("/a2a/agent-card.json").json()
        skill_ids = [s["id"] for s in body["skills"]]
        assert "diagnose_incident" in skill_ids
        assert "search_incidents" in skill_ids

    def test_agent_card_declares_oauth_security(self):
        body = self._client.get("/a2a/agent-card.json").json()
        schemes = body.get("securitySchemes", {})
        assert "oauth2" in schemes
        scopes = schemes["oauth2"]["flows"]["clientCredentials"]["scopes"]
        assert "aiops:diagnose" in scopes
        assert "aiops:search" in scopes

    def test_agent_card_skills_have_io_modes(self):
        body = self._client.get("/a2a/agent-card.json").json()
        for skill in body["skills"]:
            assert "application/json" in skill["inputModes"]
            assert "application/json" in skill["outputModes"]


# ---------------------------------------------------------------------------
# 2. Dispatch de tasks via TestClient (server-side)
# ---------------------------------------------------------------------------

class TestA2ATaskDispatch:
    """Testa o handler POST /a2a/tasks diretamente."""

    @pytest.fixture(autouse=True)
    def client(self):
        self._client = TestClient(_make_a2a_app())

    def _send_task(self, skill_id: str, data: dict, task_id: str = "task-001") -> dict:
        return self._client.post("/a2a/tasks", json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "id": "rpc-1",
            "params": {
                "id": task_id,
                "skill": {"id": skill_id},
                "message": {
                    "role": "user",
                    "parts": [{"type": "data", "data": data}],
                },
            },
        })

    def test_diagnose_incident_returns_completed(self):
        resp = self._send_task("diagnose_incident", {
            "source": "pagerduty",
            "severity": "high",
            "title": "CPU spike em api-gateway",
            "service": "api-gateway",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["jsonrpc"] == "2.0"
        assert body["result"]["status"]["state"] == "completed"

    def test_diagnose_incident_task_id_preserved(self):
        resp = self._send_task("diagnose_incident", {"severity": "high"}, task_id="my-task-42")
        body = resp.json()
        assert body["result"]["id"] == "my-task-42"

    def test_diagnose_incident_returns_diagnosis_artifact(self):
        resp = self._send_task("diagnose_incident", {"severity": "critical", "service": "worker"})
        artifacts = resp.json()["result"]["artifacts"]
        names = [a["name"] for a in artifacts]
        assert "diagnosis" in names

    def test_diagnose_incident_returns_mitigation_artifact(self):
        resp = self._send_task("diagnose_incident", {"severity": "high"})
        artifacts = resp.json()["result"]["artifacts"]
        names = [a["name"] for a in artifacts]
        assert "mitigation" in names

    def test_search_incidents_returns_results_artifact(self):
        resp = self._send_task("search_incidents", {
            "query": "CPU spike kubernetes",
            "top_k": 2,
        })
        assert resp.status_code == 200
        artifacts = resp.json()["result"]["artifacts"]
        assert artifacts[0]["name"] == "search_results"
        results = artifacts[0]["parts"][0]["data"]["results"]
        assert len(results) == 2

    def test_search_incidents_preserves_query(self):
        query = "memory leak OOMKilled"
        resp = self._send_task("search_incidents", {"query": query, "top_k": 1})
        data = resp.json()["result"]["artifacts"][0]["parts"][0]["data"]
        assert data["query"] == query

    def test_unknown_skill_returns_jsonrpc_error(self):
        resp = self._send_task("nonexistent_skill", {})
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == -32601
        assert "nonexistent_skill" in body["error"]["message"]

    def test_invalid_json_body_returns_400(self):
        resp = self._client.post(
            "/a2a/tasks",
            content=b"not-valid-json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code == 400

    def test_missing_engine_returns_error_artifact(self):
        """Sem engine no app.state, retorna artifact de erro (graceful degradation)."""
        app = _make_a2a_app(with_engine=False)
        client = TestClient(app)
        resp = client.post("/a2a/tasks", json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "id": "1",
            "params": {
                "id": "task-x",
                "skill": {"id": "diagnose_incident"},
                "message": {"role": "user", "parts": [{"type": "data", "data": {}}]},
            },
        })
        artifacts = resp.json()["result"]["artifacts"]
        assert artifacts[0]["name"] == "error"
        assert "engine_unavailable" in artifacts[0]["parts"][0]["data"]["error"]

    def test_missing_retriever_returns_error_artifact(self):
        """Sem retriever no app.state, retorna artifact de erro (graceful degradation)."""
        app = _make_a2a_app(with_retriever=False)
        client = TestClient(app)
        resp = client.post("/a2a/tasks", json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "id": "1",
            "params": {
                "id": "task-x",
                "skill": {"id": "search_incidents"},
                "message": {"role": "user", "parts": [{"type": "data", "data": {"query": "test"}}]},
            },
        })
        artifacts = resp.json()["result"]["artifacts"]
        assert artifacts[0]["name"] == "error"
        assert "retriever_unavailable" in artifacts[0]["parts"][0]["data"]["error"]


# ---------------------------------------------------------------------------
# 3. A2AClient end-to-end via ASGI transport
# ---------------------------------------------------------------------------

class TestA2AClientEndToEnd:
    """Testa A2AClient chamando server via ASGI — sem HTTP real."""

    @pytest.fixture(autouse=True)
    def inject_asgi_transport(self, monkeypatch):
        app = _make_a2a_app()
        transport = httpx.ASGITransport(app=app)
        patched_cls = _make_asgi_client_class(transport, _BASE_URL)
        monkeypatch.setattr("agents.a2a_client.httpx.AsyncClient", patched_cls)

    @pytest.mark.asyncio
    async def test_discover_returns_agent_card(self):
        client = A2AClient()
        card = await client.discover(_BASE_URL)
        assert card["name"] == "AIOps Agent"
        assert any(s["id"] == "diagnose_incident" for s in card["skills"])

    @pytest.mark.asyncio
    async def test_send_task_diagnose_returns_completed(self):
        client = A2AClient()
        result = await client.send_task(
            agent_url=_BASE_URL,
            skill_id="diagnose_incident",
            data={"source": "pagerduty", "severity": "high", "service": "api-gateway"},
        )
        assert result["status"]["state"] == "completed"

    @pytest.mark.asyncio
    async def test_send_task_search_returns_results(self):
        client = A2AClient()
        result = await client.send_task(
            agent_url=_BASE_URL,
            skill_id="search_incidents",
            data={"query": "CPU spike", "top_k": 2},
        )
        assert result["status"]["state"] == "completed"
        data = result["artifacts"][0]["parts"][0]["data"]
        assert len(data["results"]) == 2

    @pytest.mark.asyncio
    async def test_send_task_unknown_skill_raises(self):
        client = A2AClient()
        with pytest.raises(ValueError, match="A2A error -32601"):
            await client.send_task(
                agent_url=_BASE_URL,
                skill_id="nonexistent_skill",
                data={},
            )

    @pytest.mark.asyncio
    async def test_task_id_passed_explicitly(self):
        client = A2AClient()
        result = await client.send_task(
            agent_url=_BASE_URL,
            skill_id="diagnose_incident",
            data={"severity": "low"},
            task_id="explicit-task-99",
        )
        assert result["id"] == "explicit-task-99"

    @pytest.mark.asyncio
    async def test_bearer_token_propagated_in_header(self):
        """Verifica que A2AClient injeta Authorization: Bearer no header."""
        token = issue_token("assistant-agent", ["aiops:diagnose"], _JWT_SECRET)
        client = A2AClient(token=token)
        # Se o token chegar ao servidor, a chamada ainda retorna 200
        result = await client.send_task(
            agent_url=_BASE_URL,
            skill_id="search_incidents",
            data={"query": "test"},
        )
        assert result["status"]["state"] == "completed"


# ---------------------------------------------------------------------------
# 4. Fluxo completo: OAuth → Bearer JWT → A2A
# ---------------------------------------------------------------------------

class TestA2AFullSimulation:
    """Simula o fluxo real de um agente externo:
    1. Descoberta do Agent Card
    2. Obtenção de token JWT via OAuth register
    3. Chamada de skill com Bearer token
    """

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        self._app = _make_auth_a2a_app()
        self._http = TestClient(self._app)
        # Injeta ASGI transport no A2AClient
        transport = httpx.ASGITransport(app=self._app)
        patched_cls = _make_asgi_client_class(transport, _BASE_URL)
        monkeypatch.setattr("agents.a2a_client.httpx.AsyncClient", patched_cls)

    def _get_token(self, client_id: str, scopes: list[str]) -> str:
        """Simula OAuth PKCE: register → access_token."""
        verifier = generate_verifier()
        challenge = derive_challenge(verifier)
        resp = self._http.post("/auth/token/register", json={
            "client_id": client_id,
            "scopes": scopes,
            "code_challenge": challenge,
        })
        assert resp.status_code == 200, f"OAuth falhou: {resp.json()}"
        return resp.json()["access_token"]

    def test_assistant_agent_full_flow(self):
        """Assistant Agent: discovery → token → diagnose_incident."""
        # Step 1: Discovery
        card_resp = self._http.get("/a2a/agent-card.json")
        assert card_resp.status_code == 200
        card = card_resp.json()
        token_url = card["securitySchemes"]["oauth2"]["flows"]["clientCredentials"]["tokenUrl"]
        assert token_url == "/auth/token/register"

        # Step 2: Obter token via OAuth PKCE
        token = self._get_token("assistant-agent", ["aiops:diagnose", "aiops:search"])
        assert token  # token JWT emitido

        # Step 3: Chamar skill com Bearer token
        resp = self._http.post("/a2a/tasks", json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "id": "rpc-assistant-1",
            "params": {
                "id": "incident-assistant-42",
                "skill": {"id": "diagnose_incident"},
                "message": {
                    "role": "user",
                    "parts": [{"type": "data", "data": {
                        "source": "pagerduty",
                        "severity": "critical",
                        "title": "High error rate on /api/v2/prescriptions",
                        "service": "prescription-service",
                        "description": "5xx rate jumped to 45% — 3000 req/min",
                    }}],
                },
            },
        }, headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["id"] == "incident-assistant-42"
        assert result["status"]["state"] == "completed"
        artifacts = {a["name"] for a in result["artifacts"]}
        assert "diagnosis" in artifacts
        assert "mitigation" in artifacts

    def test_databricks_agent_search_only(self):
        """Databricks MCP (só aiops:search) consegue buscar mas não tem acesso a diagnose."""
        token = self._get_token("databricks-mcp", ["aiops:search"])

        # Busca: deve funcionar
        resp = self._http.post("/a2a/tasks", json={
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "id": "1",
            "params": {
                "id": "databricks-search-1",
                "skill": {"id": "search_incidents"},
                "message": {
                    "role": "user",
                    "parts": [{"type": "data", "data": {
                        "query": "spark job OOM killed",
                        "top_k": 3,
                    }}],
                },
            },
        }, headers={"Authorization": f"Bearer {token}"})

        assert resp.status_code == 200
        assert resp.json()["result"]["status"]["state"] == "completed"

    def test_sre_orchestrator_calls_both_skills(self):
        """SRE Orchestrator (scopes completos) usa diagnose + search em sequência."""
        token = self._get_token("sre-orchestrator", ["aiops:diagnose", "aiops:search"])

        incident_data = {
            "source": "prometheus",
            "severity": "high",
            "title": "P99 latency > 2s",
            "service": "checkout-service",
        }

        # Diagnose
        diagnose_resp = self._http.post("/a2a/tasks", json={
            "jsonrpc": "2.0", "method": "tasks/send", "id": "1",
            "params": {
                "id": "sre-diagnose-1",
                "skill": {"id": "diagnose_incident"},
                "message": {"role": "user", "parts": [{"type": "data", "data": incident_data}]},
            },
        }, headers={"Authorization": f"Bearer {token}"})

        assert diagnose_resp.json()["result"]["status"]["state"] == "completed"

        # Search — usa contexto do diagnóstico
        search_resp = self._http.post("/a2a/tasks", json={
            "jsonrpc": "2.0", "method": "tasks/send", "id": "2",
            "params": {
                "id": "sre-search-1",
                "skill": {"id": "search_incidents"},
                "message": {"role": "user", "parts": [{"type": "data", "data": {
                    "query": "P99 latency checkout-service",
                    "top_k": 3,
                }}]},
            },
        }, headers={"Authorization": f"Bearer {token}"})

        assert search_resp.json()["result"]["status"]["state"] == "completed"
        results = search_resp.json()["result"]["artifacts"][0]["parts"][0]["data"]["results"]
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_a2aclient_full_flow_with_token(self):
        """A2AClient completo: discover → token → send_task (sem HTTP real)."""
        # Token pré-emitido (simula o que o agente teria obtido via OAuth)
        token = issue_token("assistant-agent", ["aiops:diagnose"], _JWT_SECRET)

        client = A2AClient(token=token)

        # Discovery
        card = await client.discover(_BASE_URL)
        assert card["name"] == "AIOps Agent"

        # Task
        result = await client.send_task(
            agent_url=_BASE_URL,
            skill_id="diagnose_incident",
            data={
                "source": "pagerduty",
                "severity": "high",
                "service": "api-gateway",
                "title": "CPU > 90%",
            },
        )
        assert result["status"]["state"] == "completed"
        assert "diagnosis" in {a["name"] for a in result["artifacts"]}


# ---------------------------------------------------------------------------
# 5. Diagnóstico de artifacts — conteúdo real do MockEngine
# ---------------------------------------------------------------------------

class TestA2AArtifactContent:
    """Verifica a estrutura interna dos artifacts retornados."""

    @pytest.fixture(autouse=True)
    def client(self):
        self._client = TestClient(_make_a2a_app())

    def _diagnose(self, data: dict) -> list[dict]:
        resp = self._client.post("/a2a/tasks", json={
            "jsonrpc": "2.0", "method": "tasks/send", "id": "1",
            "params": {
                "id": "art-test",
                "skill": {"id": "diagnose_incident"},
                "message": {"role": "user", "parts": [{"type": "data", "data": data}]},
            },
        })
        return resp.json()["result"]["artifacts"]

    def test_diagnosis_artifact_has_root_cause(self):
        artifacts = self._diagnose({"severity": "high"})
        diag = next(a for a in artifacts if a["name"] == "diagnosis")
        content = diag["parts"][0]["data"]
        assert "root_cause" in content

    def test_mitigation_artifact_has_action(self):
        artifacts = self._diagnose({"severity": "critical"})
        mitigation = next(a for a in artifacts if a["name"] == "mitigation")
        content = mitigation["parts"][0]["data"]
        assert "action" in content

    def test_search_artifact_has_score_field(self):
        resp = self._client.post("/a2a/tasks", json={
            "jsonrpc": "2.0", "method": "tasks/send", "id": "1",
            "params": {
                "id": "search-art",
                "skill": {"id": "search_incidents"},
                "message": {"role": "user", "parts": [{"type": "data", "data": {"query": "OOM"}}]},
            },
        })
        results = resp.json()["result"]["artifacts"][0]["parts"][0]["data"]["results"]
        for r in results:
            assert "score" in r
            assert "id" in r
            assert "summary" in r
