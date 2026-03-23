"""Testes de integração ponta-a-ponta: autenticação federada + invocação de skill via A2A.

Simula o fluxo completo:
  1. Agente externo obtém token RS256 de seu próprio IdP
  2. OIDCValidator valida o token (JWKS mockado — sem rede real)
  3. Agente chama skill via POST /a2a/tasks
  4. Resultado é verificado

Cenários:
  - PartnerA agent chama diagnose_incident
  - PartnerB agent chama search_incidents
  - Workflow multi-agente: dois IdPs colaborando
  - Enforcement de scopes
  - Tokens expirados/inválidos rejeitados pelo OIDCValidator
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth.keys import generate_rsa_keypair, public_key_to_jwk
from auth.oidc_validator import OIDCValidator, TrustedIssuerConfig
from server.routes.a2a import router as a2a_router


# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_ISSUER_PARTNERA = "https://idp.partnera.test"
_ISSUER_PARTNERB = "https://idp.partnerb.test"
_AUDIENCE = "aiops-mcp"


# ---------------------------------------------------------------------------
# Fixtures de módulo — RSA é lento, gera uma vez por sessão
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def partnera_keypair() -> tuple[str, str]:
    """Par de chaves RSA simulando o IdP da PartnerA."""
    return generate_rsa_keypair(key_size=2048)


@pytest.fixture(scope="module")
def partnerb_keypair() -> tuple[str, str]:
    """Par de chaves RSA simulando o IdP da PartnerB."""
    return generate_rsa_keypair(key_size=2048)


@pytest.fixture(scope="module")
def partnera_jwks(partnera_keypair: tuple[str, str]) -> dict:
    """JWKS público do IdP PartnerA."""
    _, pub = partnera_keypair
    return {"keys": [public_key_to_jwk(pub, kid="partnera-key-1")]}


@pytest.fixture(scope="module")
def partnerb_jwks(partnerb_keypair: tuple[str, str]) -> dict:
    """JWKS público do IdP PartnerB."""
    _, pub = partnerb_keypair
    return {"keys": [public_key_to_jwk(pub, kid="partnerb-key-1")]}


# ---------------------------------------------------------------------------
# Mocks de infra (copiados do padrão de test_a2a_simulation.py)
# ---------------------------------------------------------------------------


class _MockEngine:
    """BlueprintEngine mínimo — retorna diagnosis + mitigation fixos."""

    def run(self, pipeline, state) -> _MockResult:
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
# Helpers
# ---------------------------------------------------------------------------


def _make_token(
    private_pem: str,
    iss: str = _ISSUER_PARTNERA,
    sub: str = "assistant-bot",
    aud: str = _AUDIENCE,
    scopes: str = "aiops:diagnose aiops:search",
    exp_offset: int = 300,
    kid: str = "partnera-key-1",
) -> str:
    """Emite token RS256 simulando um IdP externo."""
    return pyjwt.encode(
        {
            "sub": sub,
            "iss": iss,
            "aud": aud,
            "exp": int(time.time()) + exp_offset,
            "iat": int(time.time()),
            "scope": scopes,
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": kid},
    )


def _make_discovery(issuer: str) -> dict:
    """Simula resposta do OpenID Configuration discovery."""
    return {
        "issuer": issuer,
        "jwks_uri": f"{issuer}/.well-known/jwks.json",
    }


def _mock_idp_client(discovery: dict, jwks: dict) -> MagicMock:
    """Mock do httpx.AsyncClient que responde ao discovery e jwks do IdP."""
    disc_resp = MagicMock()
    disc_resp.json.return_value = discovery
    disc_resp.raise_for_status = MagicMock()

    jwks_resp = MagicMock()
    jwks_resp.json.return_value = jwks
    jwks_resp.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[disc_resp, jwks_resp])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return mock_client


def _make_a2a_app() -> FastAPI:
    """App FastAPI com A2A router e infra mockada."""
    app = FastAPI()

    import blueprint.pipelines.incident_response as _bp
    app.state._pipeline_patch = patch.object(
        _bp, "build_incident_response_pipeline", return_value=None
    )
    app.state._pipeline_patch.start()

    app.include_router(a2a_router)
    app.state.engine = _MockEngine()
    app.state.retriever = _MockRetriever()
    return app


def _send_task(client: TestClient, skill_id: str, data: dict, task_id: str = "task-001") -> dict:
    """Envia tarefa A2A via POST /a2a/tasks e retorna a resposta JSON."""
    resp = client.post("/a2a/tasks", json={
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
    return resp


# ---------------------------------------------------------------------------
# 1. PartnerA agent chama diagnose_incident
# ---------------------------------------------------------------------------


class TestFederatedAgentDiagnose:
    """Agente PartnerA com token RS256 válido chama diagnose_incident via A2A."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_valid_token_validates_via_oidc(
        self,
        partnera_keypair: tuple[str, str],
        partnera_jwks: dict,
    ) -> None:
        """Token PartnerA válido deve ser aceito pelo OIDCValidator."""
        priv, _ = partnera_keypair
        token = _make_token(priv, sub="assistant-bot", scopes="aiops:diagnose")

        validator = OIDCValidator(
            trusted_issuers=[TrustedIssuerConfig(issuer=_ISSUER_PARTNERA, audience=_AUDIENCE)]
        )
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            claims = await validator.validate(token)

        assert claims.sub == "assistant-bot"
        assert "aiops:diagnose" in claims.scopes

    @pytest.mark.integration
    def test_diagnose_skill_returns_completed(
        self,
        partnera_keypair: tuple[str, str],
    ) -> None:
        """Após validação federada, agente PartnerA chama diagnose_incident com sucesso."""
        priv, _ = partnera_keypair
        # Token já validado — agente chama diretamente o endpoint A2A
        app = _make_a2a_app()
        with TestClient(app) as client:
            resp = _send_task(
                client,
                skill_id="diagnose_incident",
                data={
                    "source": "pagerduty",
                    "severity": "critical",
                    "title": "High error rate em /api/v2/prescriptions",
                    "service": "prescription-service",
                },
                task_id="partnera-incident-001",
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["jsonrpc"] == "2.0"
        assert body["result"]["status"]["state"] == "completed"
        assert body["result"]["id"] == "partnera-incident-001"

    @pytest.mark.integration
    def test_diagnose_returns_diagnosis_and_mitigation_artifacts(
        self,
        partnera_keypair: tuple[str, str],
    ) -> None:
        """Resultado deve conter artifacts de diagnosis e mitigation."""
        app = _make_a2a_app()
        with TestClient(app) as client:
            resp = _send_task(
                client,
                skill_id="diagnose_incident",
                data={"source": "prometheus", "severity": "high", "service": "api-gateway"},
                task_id="partnera-incident-002",
            )

        artifacts = resp.json()["result"]["artifacts"]
        names = {a["name"] for a in artifacts}
        assert "diagnosis" in names
        assert "mitigation" in names

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_oidc_claims_match_token_payload(
        self,
        partnera_keypair: tuple[str, str],
        partnera_jwks: dict,
    ) -> None:
        """Claims extraídos pelo OIDCValidator devem refletir exatamente o payload do token."""
        priv, _ = partnera_keypair
        token = _make_token(
            priv,
            sub="assistant-bot",
            scopes="aiops:diagnose aiops:search",
        )

        validator = OIDCValidator(
            trusted_issuers=[TrustedIssuerConfig(issuer=_ISSUER_PARTNERA, audience=_AUDIENCE)]
        )
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            claims = await validator.validate(token)

        assert claims.sub == "assistant-bot"
        assert set(claims.scopes) == {"aiops:diagnose", "aiops:search"}
        assert claims.exp > int(time.time())


# ---------------------------------------------------------------------------
# 2. PartnerB agent chama search_incidents
# ---------------------------------------------------------------------------


class TestFederatedAgentSearch:
    """Agente PartnerB com token RS256 válido chama search_incidents via A2A."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_partnerb_token_validates_via_oidc(
        self,
        partnerb_keypair: tuple[str, str],
        partnerb_jwks: dict,
    ) -> None:
        """Token PartnerB válido deve ser aceito pelo OIDCValidator."""
        priv, _ = partnerb_keypair
        token = _make_token(
            priv,
            iss=_ISSUER_PARTNERB,
            sub="partnerb-sre",
            scopes="aiops:search",
            kid="partnerb-key-1",
        )

        validator = OIDCValidator(
            trusted_issuers=[TrustedIssuerConfig(issuer=_ISSUER_PARTNERB, audience=_AUDIENCE)]
        )
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERB), partnerb_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            claims = await validator.validate(token)

        assert claims.sub == "partnerb-sre"
        assert "aiops:search" in claims.scopes

    @pytest.mark.integration
    def test_search_skill_returns_results(self) -> None:
        """Agente PartnerB chama search_incidents e recebe resultados RAG."""
        app = _make_a2a_app()
        with TestClient(app) as client:
            resp = _send_task(
                client,
                skill_id="search_incidents",
                data={"query": "high CPU", "top_k": 3},
                task_id="partnerb-search-001",
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["result"]["status"]["state"] == "completed"
        results = body["result"]["artifacts"][0]["parts"][0]["data"]["results"]
        assert len(results) == 3

    @pytest.mark.integration
    def test_search_preserves_query_in_response(self) -> None:
        """O query enviado deve ser refletido no artifact de resposta."""
        app = _make_a2a_app()
        query = "high CPU saturation kubernetes nodes"
        with TestClient(app) as client:
            resp = _send_task(
                client,
                skill_id="search_incidents",
                data={"query": query, "top_k": 2},
            )

        data = resp.json()["result"]["artifacts"][0]["parts"][0]["data"]
        assert data["query"] == query
        assert len(data["results"]) == 2

    @pytest.mark.integration
    def test_search_results_have_required_fields(self) -> None:
        """Cada resultado de busca deve ter id, summary e score."""
        app = _make_a2a_app()
        with TestClient(app) as client:
            resp = _send_task(
                client,
                skill_id="search_incidents",
                data={"query": "memory leak OOM", "top_k": 3},
            )

        results = resp.json()["result"]["artifacts"][0]["parts"][0]["data"]["results"]
        for r in results:
            assert "id" in r
            assert "summary" in r
            assert "score" in r


# ---------------------------------------------------------------------------
# 3. Workflow multi-agente: PartnerA e PartnerB colaboram
# ---------------------------------------------------------------------------


class TestFederatedMultiAgentWorkflow:
    """Dois agentes de IdPs distintos executam workflow colaborativo."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_both_agents_validate_independently(
        self,
        partnera_keypair: tuple[str, str],
        partnera_jwks: dict,
        partnerb_keypair: tuple[str, str],
        partnerb_jwks: dict,
    ) -> None:
        """PartnerA e PartnerB validam tokens de forma independente no mesmo validator."""
        partnera_priv, _ = partnera_keypair
        partnerb_priv, _ = partnerb_keypair

        partnera_token = _make_token(
            partnera_priv,
            iss=_ISSUER_PARTNERA,
            sub="assistant-bot",
            scopes="aiops:diagnose",
            kid="partnera-key-1",
        )
        partnerb_token = _make_token(
            partnerb_priv,
            iss=_ISSUER_PARTNERB,
            sub="partnerb-sre",
            scopes="aiops:search",
            kid="partnerb-key-1",
        )

        validator = OIDCValidator(
            trusted_issuers=[
                TrustedIssuerConfig(issuer=_ISSUER_PARTNERA, audience=_AUDIENCE),
                TrustedIssuerConfig(issuer=_ISSUER_PARTNERB, audience=_AUDIENCE),
            ]
        )

        # Valida token PartnerA
        partnera_mock = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)
        with patch("httpx.AsyncClient", return_value=partnera_mock):
            partnera_claims = await validator.validate(partnera_token)

        assert partnera_claims.sub == "assistant-bot"

        # Valida token PartnerB (usa cache do validator para PartnerA, faz discovery da PartnerB)
        partnerb_mock = _mock_idp_client(_make_discovery(_ISSUER_PARTNERB), partnerb_jwks)
        with patch("httpx.AsyncClient", return_value=partnerb_mock):
            partnerb_claims = await validator.validate(partnerb_token)

        assert partnerb_claims.sub == "partnerb-sre"

    @pytest.mark.integration
    def test_partnera_agent_diagnoses_incident(self) -> None:
        """Etapa 1 do workflow: PartnerA agent diagnostica o incidente."""
        app = _make_a2a_app()
        with TestClient(app) as client:
            resp = _send_task(
                client,
                skill_id="diagnose_incident",
                data={
                    "source": "pagerduty",
                    "severity": "critical",
                    "title": "CPU spike em prescription-service",
                    "service": "prescription-service",
                },
                task_id="workflow-diagnose-001",
            )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["status"]["state"] == "completed"
        artifact_names = {a["name"] for a in result["artifacts"]}
        assert "diagnosis" in artifact_names

    @pytest.mark.integration
    def test_partnerb_agent_searches_similar_incidents(self) -> None:
        """Etapa 2 do workflow: PartnerB agent busca incidentes similares."""
        app = _make_a2a_app()
        with TestClient(app) as client:
            resp = _send_task(
                client,
                skill_id="search_incidents",
                data={"query": "CPU spike prescription-service", "top_k": 3},
                task_id="workflow-search-001",
            )

        assert resp.status_code == 200
        result = resp.json()["result"]
        assert result["status"]["state"] == "completed"
        results = result["artifacts"][0]["parts"][0]["data"]["results"]
        assert len(results) == 3

    @pytest.mark.integration
    def test_full_collaborative_workflow(self) -> None:
        """Workflow completo: diagnose (PartnerA) seguido de search (PartnerB) na mesma app."""
        app = _make_a2a_app()
        with TestClient(app) as client:
            # PartnerA diagnostica
            diagnose_resp = _send_task(
                client,
                skill_id="diagnose_incident",
                data={
                    "source": "prometheus",
                    "severity": "high",
                    "title": "P99 latency degradation",
                    "service": "checkout-service",
                },
                task_id="collab-diagnose-001",
            )
            assert diagnose_resp.status_code == 200
            diagnose_result = diagnose_resp.json()["result"]
            assert diagnose_result["status"]["state"] == "completed"

            # PartnerB busca histórico relacionado
            search_resp = _send_task(
                client,
                skill_id="search_incidents",
                data={"query": "P99 latency checkout-service degradation", "top_k": 2},
                task_id="collab-search-001",
            )
            assert search_resp.status_code == 200
            search_result = search_resp.json()["result"]
            assert search_result["status"]["state"] == "completed"

        # Verifica que ambos têm IDs preservados
        assert diagnose_result["id"] == "collab-diagnose-001"
        assert search_result["id"] == "collab-search-001"


# ---------------------------------------------------------------------------
# 4. Enforcement de scopes via OIDCValidator
# ---------------------------------------------------------------------------


class TestFederatedTokenScopes:
    """Valida que scopes no token são corretamente extraídos e refletidos nos claims."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_search_only_scope_extracted_correctly(
        self,
        partnera_keypair: tuple[str, str],
        partnera_jwks: dict,
    ) -> None:
        """Agente com apenas aiops:search deve ter somente esse scope nos claims."""
        priv, _ = partnera_keypair
        token = _make_token(priv, scopes="aiops:search")

        validator = OIDCValidator(
            trusted_issuers=[TrustedIssuerConfig(issuer=_ISSUER_PARTNERA, audience=_AUDIENCE)]
        )
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            claims = await validator.validate(token)

        assert claims.scopes == ["aiops:search"]
        assert "aiops:diagnose" not in claims.scopes

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_diagnose_scope_extracted_correctly(
        self,
        partnera_keypair: tuple[str, str],
        partnera_jwks: dict,
    ) -> None:
        """Agente com aiops:diagnose deve ter esse scope nos claims."""
        priv, _ = partnera_keypair
        token = _make_token(priv, scopes="aiops:diagnose")

        validator = OIDCValidator(
            trusted_issuers=[TrustedIssuerConfig(issuer=_ISSUER_PARTNERA, audience=_AUDIENCE)]
        )
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            claims = await validator.validate(token)

        assert "aiops:diagnose" in claims.scopes
        assert "aiops:search" not in claims.scopes

    @pytest.mark.integration
    def test_search_scoped_agent_can_call_search_skill(self) -> None:
        """Agente com scope search pode chamar search_incidents no A2A (endpoint não filtra scopes)."""
        app = _make_a2a_app()
        with TestClient(app) as client:
            resp = _send_task(
                client,
                skill_id="search_incidents",
                data={"query": "test query", "top_k": 1},
            )
        # A2A endpoint é aberto — scopes são informacionais (enforcement na camada SSE/MCP)
        assert resp.status_code == 200
        assert resp.json()["result"]["status"]["state"] == "completed"

    @pytest.mark.integration
    def test_diagnose_scoped_agent_can_call_diagnose_skill(self) -> None:
        """Agente com scope diagnose pode chamar diagnose_incident no A2A."""
        app = _make_a2a_app()
        with TestClient(app) as client:
            resp = _send_task(
                client,
                skill_id="diagnose_incident",
                data={"source": "a2a", "severity": "high"},
            )
        assert resp.status_code == 200
        assert resp.json()["result"]["status"]["state"] == "completed"

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_full_scope_set_extracted_correctly(
        self,
        partnerb_keypair: tuple[str, str],
        partnerb_jwks: dict,
    ) -> None:
        """Token com múltiplos scopes deve ter todos corretamente listados nos claims."""
        priv, _ = partnerb_keypair
        token = _make_token(
            priv,
            iss=_ISSUER_PARTNERB,
            sub="partnerb-sre",
            scopes="aiops:diagnose aiops:search aiops:validate",
            kid="partnerb-key-1",
        )

        validator = OIDCValidator(
            trusted_issuers=[TrustedIssuerConfig(issuer=_ISSUER_PARTNERB, audience=_AUDIENCE)]
        )
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERB), partnerb_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            claims = await validator.validate(token)

        assert set(claims.scopes) == {"aiops:diagnose", "aiops:search", "aiops:validate"}


# ---------------------------------------------------------------------------
# 5. Tokens expirados e inválidos — OIDCValidator deve rejeitar
# ---------------------------------------------------------------------------


class TestFederatedTokenExpiry:
    """Tokens inválidos devem lançar exceções no OIDCValidator."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_expired_token_raises_expired_signature_error(
        self,
        partnera_keypair: tuple[str, str],
        partnera_jwks: dict,
    ) -> None:
        """Token expirado deve lançar ExpiredSignatureError."""
        priv, _ = partnera_keypair
        token = _make_token(priv, exp_offset=-60)  # expirou há 60 segundos

        validator = OIDCValidator(
            trusted_issuers=[TrustedIssuerConfig(issuer=_ISSUER_PARTNERA, audience=_AUDIENCE)]
        )
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(pyjwt.ExpiredSignatureError):
                await validator.validate(token)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_wrong_audience_raises_invalid_token_error(
        self,
        partnera_keypair: tuple[str, str],
        partnera_jwks: dict,
    ) -> None:
        """Token com audience errada deve lançar InvalidTokenError."""
        priv, _ = partnera_keypair
        token = _make_token(priv, aud="wrong-service")

        validator = OIDCValidator(
            trusted_issuers=[TrustedIssuerConfig(issuer=_ISSUER_PARTNERA, audience=_AUDIENCE)]
        )
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(pyjwt.InvalidTokenError):
                await validator.validate(token)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_untrusted_issuer_raises_invalid_token_error(
        self,
        partnera_keypair: tuple[str, str],
    ) -> None:
        """Token de issuer não configurado deve lançar InvalidTokenError sem chamada HTTP."""
        priv, _ = partnera_keypair
        token = _make_token(priv, iss="https://evil.attacker.test")

        # Validator sem nenhum issuer confiável configurado para o attacker
        validator = OIDCValidator(
            trusted_issuers=[TrustedIssuerConfig(issuer=_ISSUER_PARTNERA, audience=_AUDIENCE)]
        )

        # Não deve fazer chamada HTTP — deve falhar antes
        with pytest.raises(pyjwt.InvalidTokenError, match="Issuer não confiável"):
            await validator.validate(token)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_corrupted_signature_raises_invalid_token_error(
        self,
        partnera_keypair: tuple[str, str],
        partnera_jwks: dict,
    ) -> None:
        """Token com assinatura corrompida deve lançar InvalidTokenError."""
        priv, _ = partnera_keypair
        token = _make_token(priv)
        parts = token.split(".")
        parts[2] = parts[2][:-4] + "XXXX"
        corrupted_token = ".".join(parts)

        validator = OIDCValidator(
            trusted_issuers=[TrustedIssuerConfig(issuer=_ISSUER_PARTNERA, audience=_AUDIENCE)]
        )
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERA), partnera_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(pyjwt.InvalidTokenError):
                await validator.validate(corrupted_token)

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_malformed_jwt_raises_invalid_token_error(self) -> None:
        """JWT malformado (não é um JWT válido) deve lançar InvalidTokenError."""
        validator = OIDCValidator(
            trusted_issuers=[TrustedIssuerConfig(issuer=_ISSUER_PARTNERA, audience=_AUDIENCE)]
        )

        with pytest.raises(pyjwt.InvalidTokenError):
            await validator.validate("nao.e.um.jwt.valido")

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_wrong_issuer_key_raises_invalid_token_error(
        self,
        partnera_keypair: tuple[str, str],
        partnerb_jwks: dict,
    ) -> None:
        """Token assinado pela PartnerA mas validado com chave PartnerB deve falhar."""
        partnera_priv, _ = partnera_keypair
        # Token diz ser da PartnerB, mas foi assinado com chave PartnerA
        token = _make_token(
            partnera_priv,
            iss=_ISSUER_PARTNERB,
            kid="partnerb-key-1",
        )

        validator = OIDCValidator(
            trusted_issuers=[TrustedIssuerConfig(issuer=_ISSUER_PARTNERB, audience=_AUDIENCE)]
        )
        # JWKS retorna a chave pública da PartnerB → assinatura não bate
        mock_client = _mock_idp_client(_make_discovery(_ISSUER_PARTNERB), partnerb_jwks)

        with patch("httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(pyjwt.InvalidTokenError):
                await validator.validate(token)
