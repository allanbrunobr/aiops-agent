"""Testes para core/ — Story 1.2."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import pytest

from adapters.persistence.in_memory_store import InMemoryStore
from application.ports.incident_store import IncidentStore
from core.credentials import EnvCredentialProvider
from core.protocols import CredentialProvider
from domain.alert import NormalizedAlert
from domain.incident_state import IncidentState
from server.webhook.schemas import NormalizedAlertDTO, normalize_severity


def _make_alert(**overrides) -> NormalizedAlert:
    """Helper para criar NormalizedAlert de teste."""
    defaults = {
        "source": "pagerduty",
        "source_id": "PD-12345",
        "correlation_id": "inc-20260308-a7f3b2c1",
        "title": "High Error Rate",
        "description": "payment-svc error rate > 5%",
        "severity": "critical",
        "alert_type": "HighErrorRate",
        "timestamp": datetime(2026, 3, 8, 14, 0, 0, tzinfo=timezone.utc),
        "detection_timestamp": datetime(2026, 3, 8, 14, 0, 5, tzinfo=timezone.utc),
        "service": "payment-svc",
    }
    defaults.update(overrides)
    return NormalizedAlert(**defaults)


def _make_state(**overrides) -> IncidentState:
    """Helper para criar IncidentState de teste."""
    alert = _make_alert()
    defaults = {
        "incident_id": "inc-20260308-a7f3b2c1",
        "correlation_id": alert.correlation_id,
        "alert": alert,
        "received_at": datetime(2026, 3, 8, 14, 0, 5, tzinfo=timezone.utc),
        "started_at": alert.timestamp,
    }
    defaults.update(overrides)
    return IncidentState(**defaults)


class TestNormalizedAlert:
    """Testes para NormalizedAlert."""

    def test_valid_creation(self):
        """Criacao valida com todos os campos."""
        alert = _make_alert()
        assert alert.source == "pagerduty"
        assert alert.severity == "critical"
        assert alert.service == "payment-svc"

    def test_invalid_severity_rejected_at_dto_boundary(self):
        """Severity invalida deve gerar erro no DTO/helper da borda (ADR 0003)."""
        with pytest.raises(Exception, match="severity must be one of"):
            normalize_severity("extreme")

    def test_dto_severity_normalized_to_lowercase(self):
        """DTO da borda normaliza severity para minusculo."""
        dto = NormalizedAlertDTO(
            source="pagerduty",
            source_id="PD-1",
            correlation_id="inc-20260308-a7f3b2c1",
            title="t",
            description="d",
            severity="WARNING",
            alert_type="HighErrorRate",
            timestamp=datetime(2026, 3, 8, tzinfo=timezone.utc),
            detection_timestamp=datetime(2026, 3, 8, tzinfo=timezone.utc),
        )
        assert dto.severity == "warning"
        assert dto.to_domain().severity == "warning"

    def test_json_round_trip(self):
        """Serializacao JSON ida-e-volta via stdlib (dataclasses.asdict + json)."""
        alert = _make_alert()
        payload = dataclasses.asdict(alert)
        json_str = json.dumps(payload, default=str)
        restored_dict = json.loads(json_str)
        # Reconstrói datetime a partir do ISO string
        restored_dict["timestamp"] = datetime.fromisoformat(restored_dict["timestamp"])
        restored_dict["detection_timestamp"] = datetime.fromisoformat(
            restored_dict["detection_timestamp"]
        )
        restored = NormalizedAlert(**restored_dict)
        assert restored == alert

    def test_optional_fields_default_none(self):
        """Campos opcionais devem ser None por default."""
        alert = _make_alert(service=None, component=None, environment=None)
        assert alert.service is None
        assert alert.component is None
        assert alert.environment is None


class TestIncidentState:
    """Testes para IncidentState."""

    def test_creation_from_alert(self):
        """IncidentState criado a partir de NormalizedAlert."""
        state = _make_state()
        assert state.incident_id == "inc-20260308-a7f3b2c1"
        assert state.alert.source == "pagerduty"
        assert state.state == "DETECTING"

    def test_json_round_trip(self):
        """Serializacao JSON ida-e-volta via stdlib (dataclasses.asdict + json)."""
        state = _make_state()
        payload = dataclasses.asdict(state)
        json_str = json.dumps(payload, default=str)
        restored_dict = json.loads(json_str)
        # Reconstrói datetimes (alert + state) a partir do ISO string
        alert_dict = restored_dict["alert"]
        alert_dict["timestamp"] = datetime.fromisoformat(alert_dict["timestamp"])
        alert_dict["detection_timestamp"] = datetime.fromisoformat(alert_dict["detection_timestamp"])
        restored_dict["alert"] = NormalizedAlert(**alert_dict)
        restored_dict["received_at"] = datetime.fromisoformat(restored_dict["received_at"])
        restored_dict["started_at"] = datetime.fromisoformat(restored_dict["started_at"])
        restored = IncidentState(**restored_dict)
        assert restored.incident_id == state.incident_id
        assert restored.alert.source == state.alert.source

    def test_default_flags(self):
        """Flags de modo devem ser False por default."""
        state = _make_state()
        assert state.no_llm is False
        assert state.no_rag is False

    def test_immutable_replace(self):
        """Campos do pipeline devem ser atualizaveis via dataclasses.replace."""
        state = _make_state()
        updated = dataclasses.replace(state, diagnosis={"root_cause": "pool exhaustion"})
        assert updated.diagnosis == {"root_cause": "pool exhaustion"}
        assert state.diagnosis is None  # Original inalterado


class TestInMemoryStore:
    """Testes para InMemoryStore."""

    def test_save_and_get(self):
        """save/get lifecycle basico."""
        store = InMemoryStore()
        state = _make_state()
        store.save("inc-001", state)
        retrieved = store.get("inc-001")
        assert retrieved is not None
        assert retrieved.incident_id == state.incident_id

    def test_get_nonexistent_returns_none(self):
        """get de ID inexistente retorna None."""
        store = InMemoryStore()
        assert store.get("nonexistent") is None

    def test_list_active_filters_terminal(self):
        """list_active deve filtrar estados terminais."""
        store = InMemoryStore()
        active = _make_state(state="DIAGNOSING")
        resolved = _make_state(state="RESOLVED")
        store.save("active-1", active)
        store.save("resolved-1", resolved)
        actives = store.list_active()
        assert len(actives) == 1
        assert actives[0][0] == "active-1"

    def test_fresh_store_empty(self):
        """Store fresco deve ter estado vazio (isolamento de pipeline)."""
        store = InMemoryStore()
        assert store.list_active() == []
        assert store.get("anything") is None

    def test_save_overwrites(self):
        """save com mesmo ID sobrescreve estado anterior."""
        store = InMemoryStore()
        state1 = _make_state(state="DETECTING")
        state2 = _make_state(state="DIAGNOSING")
        store.save("inc-001", state1)
        store.save("inc-001", state2)
        assert store.get("inc-001").state == "DIAGNOSING"


class TestEnvCredentialProvider:
    """Testes para EnvCredentialProvider."""

    def test_reads_from_env(self, monkeypatch):
        """get deve ler valor da variavel de ambiente."""
        monkeypatch.setenv("TEST_API_KEY", "secret-value-123")
        creds = EnvCredentialProvider()
        assert creds.get("TEST_API_KEY") == "secret-value-123"

    def test_missing_key_returns_none(self):
        """get de chave inexistente retorna None."""
        creds = EnvCredentialProvider()
        assert creds.get("NONEXISTENT_KEY_XYZ_12345") is None


class TestProtocolCompliance:
    """Testes de conformidade com Protocols."""

    def test_in_memory_store_is_incident_store(self):
        """InMemoryStore deve satisfazer IncidentStore Protocol."""
        assert isinstance(InMemoryStore(), IncidentStore)

    def test_env_credential_provider_is_credential_provider(self):
        """EnvCredentialProvider deve satisfazer CredentialProvider Protocol."""
        assert isinstance(EnvCredentialProvider(), CredentialProvider)


class TestPipelineIsolation:
    """Testes de isolamento entre runs de pipeline."""

    def test_two_states_independent(self):
        """Duas instancias de IncidentState nao compartilham estado."""
        state1 = _make_state(incident_id="inc-001")
        state2 = _make_state(incident_id="inc-002")
        updated1 = dataclasses.replace(state1, diagnosis={"root_cause": "pool"})
        assert state2.diagnosis is None
        assert updated1.diagnosis is not None
