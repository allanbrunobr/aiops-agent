from __future__ import annotations

"""Testes unitários do endpoint /demo/api/narrative/{scenario}.

Usa fastapi.testclient.TestClient diretamente sobre o app exposto em
`interfaces/demo/server.py` — sem subir uvicorn. Cobre cenários
conhecidos, 404 e estrutura de serialização das fases.
"""

import pytest
from fastapi.testclient import TestClient

from interfaces.demo.server import app

_EXPECTED_PHASES = {"PLAYING", "DEGRADING", "WARNING", "ALERT", "INCIDENT", "RESOLVED"}
_EXPECTED_TOP_LEVEL_KEYS = {
    "scenario_name",
    "app_name",
    "app_context",
    "baseline_revenue_per_min",
    "phases",
}
_KNOWN_SCENARIOS = ["payment-pool-exhaustion", "payment-pool-recovery"]


@pytest.fixture(scope="module")
def client() -> TestClient:
    """TestClient compartilhado — app é stateless para este endpoint."""
    return TestClient(app)


@pytest.mark.unit
@pytest.mark.parametrize("scenario", _KNOWN_SCENARIOS)
def test_narrative_endpoint_returns_200_for_known_scenario(
    client: TestClient, scenario: str
) -> None:
    """Cenários registrados devolvem 200 com payload completo do dataclass."""
    response = client.get(f"/demo/api/narrative/{scenario}")
    assert response.status_code == 200

    payload = response.json()
    assert _EXPECTED_TOP_LEVEL_KEYS <= set(payload.keys())
    assert payload["scenario_name"] == scenario
    assert isinstance(payload["phases"], dict)
    assert isinstance(payload["baseline_revenue_per_min"], int)


@pytest.mark.unit
def test_narrative_endpoint_returns_404_for_unknown(client: TestClient) -> None:
    """Cenário inexistente devolve 404 e mensagem PT-BR estruturada."""
    response = client.get("/demo/api/narrative/nope")
    assert response.status_code == 404
    assert response.json() == {"error": "Narrativa 'nope' não encontrada."}


@pytest.mark.unit
def test_narrative_endpoint_phases_serialized_correctly(client: TestClient) -> None:
    """As 6 fases canônicas estão presentes no payload serializado.

    Cobre todos os campos consumidos pelo frontend (`story.js`):
    timeline_label, business_impact_per_min, impact_color, hero_text e
    hero_lead_time_seconds. Falha aqui = quebra silenciosa de UI.
    """
    response = client.get("/demo/api/narrative/payment-pool-exhaustion")
    assert response.status_code == 200

    phases = response.json()["phases"]
    assert set(phases.keys()) == _EXPECTED_PHASES
    for phase_name, phase_payload in phases.items():
        assert "timeline_label" in phase_payload, phase_name
        assert "business_impact_per_min" in phase_payload, phase_name
        assert "impact_color" in phase_payload, phase_name
        assert "hero_text" in phase_payload, phase_name
        assert "hero_lead_time_seconds" in phase_payload, phase_name


@pytest.mark.unit
def test_narrative_endpoint_warning_slack_message_shape(client: TestClient) -> None:
    """A fase WARNING traz `slack_message` com os campos consumidos pela UI.

    Schema esperado por `story.js::appendSlackCard`: channel, author, title,
    body, severity, timestamp_label.
    """
    response = client.get("/demo/api/narrative/payment-pool-exhaustion")
    assert response.status_code == 200

    warning = response.json()["phases"]["WARNING"]
    msg = warning.get("slack_message")
    assert msg is not None, "WARNING deve disparar mensagem Slack"
    expected_keys = {"channel", "author", "title", "body", "severity", "timestamp_label"}
    assert expected_keys <= set(msg.keys())
    assert msg["severity"] == "warning"
