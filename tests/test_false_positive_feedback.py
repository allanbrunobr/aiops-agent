"""Feedback de falso positivo marca o padrão de conhecimento como negativo (4B-14).

P3-9 (docs/analise-20261109.md) — três quebras independentes:
1. KnowledgePattern não tinha `source_incident_id`, e a rota de criação não o
   repassava: a busca primária nunca achava nada;
2. o fallback lia `incident.alert.affected_services`, que não existe em
   NormalizedAlert (o campo é `service`): AttributeError engolido;
3. a store vinha de um global `interfaces.http.app.knowledge_store`, sempre
   None: o bloco nem executava.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from domain.alert import NormalizedAlert
from domain.incident_state import IncidentState
from rag.knowledge_store import KnowledgeStore
from rag.models import KnowledgePattern

pytestmark = pytest.mark.unit
CID = "inc-20261110-abcd1234"


def _incident(service="payment-svc", diagnosis=None) -> IncidentState:
    now = datetime.now(timezone.utc)
    alert = NormalizedAlert(source="grafana", source_id="s", correlation_id=CID, title="t",
                            description="d", severity="critical", alert_type="grafana.firing",
                            timestamp=now, detection_timestamp=now, service=service)
    return IncidentState(incident_id=CID, correlation_id=CID, alert=alert, received_at=now,
                         started_at=now, diagnosis=diagnosis)


def _pattern(pid, source=None, service="payment-svc", cause="pool exhausted"):
    return KnowledgePattern(pattern_id=pid, service=service, symptoms=["s"], root_cause=cause,
                            resolution="r", source_incident_id=source)


def test_pattern_keeps_source_incident_id_across_persistence(tmp_path):
    store = KnowledgeStore(storage_path=str(tmp_path / "p.json"))
    store.add_pattern(_pattern("p1", source=CID))
    reloaded = KnowledgeStore(storage_path=str(tmp_path / "p.json"))
    reloaded.load_from_disk()
    assert reloaded.get_all_patterns()[0].source_incident_id == CID


def test_marks_pattern_found_by_source_incident_id(tmp_path):
    from interfaces.http.handlers.false_positive import handle_false_positive_side_effects

    store = KnowledgeStore(storage_path=str(tmp_path / "p.json"))
    store.add_pattern(_pattern("p1", source=CID))
    asyncio.run(handle_false_positive_side_effects(CID, "fp", _incident(), knowledge_store=store))
    assert store.get_all_patterns()[0].is_negative_example is True


def test_fallback_uses_alert_service_and_root_cause(tmp_path):
    from interfaces.http.handlers.false_positive import handle_false_positive_side_effects

    store = KnowledgeStore(storage_path=str(tmp_path / "p.json"))
    store.add_pattern(_pattern("p2", source=None))
    incident = _incident(diagnosis={"root_cause": "pool exhausted"})
    asyncio.run(handle_false_positive_side_effects(CID, "fp", incident, knowledge_store=store))
    assert store.get_all_patterns()[0].is_negative_example is True


def test_create_pattern_route_passes_source_incident_id(tmp_path):
    from interfaces.http.routes.knowledge import get_knowledge_store, router

    store = KnowledgeStore(storage_path=str(tmp_path / "p.json"))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_knowledge_store] = lambda: store
    resp = TestClient(app).post("/api/v1/knowledge/patterns", json={
        "service": "payment-svc", "symptoms": ["timeouts"], "root_cause": "pool exhausted",
        "resolution": "raise pool", "source_incident_id": CID,
    })
    assert resp.status_code == 201, resp.text
    assert store.get_all_patterns()[0].source_incident_id == CID


def test_accuracy_route_marks_pattern_via_app_state(tmp_path):
    from application.ports.incident_store import IncidentStore  # noqa: F401
    from interfaces.http.api.accuracy import get_store, router

    class _Store:
        def __init__(self):
            self.items = {CID: dataclasses.replace(_incident())}

        def get(self, cid):
            return self.items.get(cid)

        def save(self, cid, state):
            self.items[cid] = state

        def list_all(self):
            return list(self.items.values())

    knowledge = KnowledgeStore(storage_path=str(tmp_path / "p.json"))
    knowledge.add_pattern(_pattern("p1", source=CID))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_store] = _Store
    app.state.knowledge_store = knowledge
    resp = TestClient(app).patch(f"/api/v1/incidents/{CID}/accuracy",
                                 json={"accuracy": "false_positive", "notes": "noise"})
    assert resp.status_code == 200, resp.text
    assert knowledge.get_all_patterns()[0].is_negative_example is True
