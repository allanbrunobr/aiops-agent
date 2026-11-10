"""Grafana: severidade vem do label; notificação `resolved` não abre pipeline.

P2 — B10/4B-06 (docs/analise-20261109.md): o mapa de severidade só conhecia
status do Grafana ("firing", "pending", ...), não valores de label. Com
`severity: warning` (ou info) e status firing, o alerta saía `critical`.
Notificações `resolved` eram submetidas como incidente novo.
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from interfaces.http.webhook.normalizers.grafana import GrafanaNormalizer
from tests.test_webhook_router import PAYLOAD, _app, _post, _Runner


def _payload(severity: str, status: str = "firing") -> dict:
    p = copy.deepcopy(PAYLOAD)
    p["alerts"][0]["status"] = status
    p["status"] = status
    if severity:
        p["alerts"][0]["labels"]["severity"] = severity
    else:
        p["alerts"][0]["labels"].pop("severity", None)
    return p


@pytest.mark.unit
class TestGrafanaSeverity:
    @pytest.mark.parametrize(("label", "expected"), [
        ("critical", "critical"), ("error", "error"), ("high", "error"),
        ("warning", "warning"), ("medium", "warning"), ("info", "info"), ("low", "info"),
        ("WARNING", "warning"),
    ])
    def test_label_drives_severity(self, label, expected):
        assert GrafanaNormalizer().normalize(_payload(label)).severity == expected

    def test_missing_label_on_firing_stays_critical(self):
        """Sem label, firing continua conservador (critical)."""
        assert GrafanaNormalizer().normalize(_payload("")).severity == "critical"


@pytest.mark.unit
class TestResolvedNotification:
    def test_resolved_is_accepted_but_not_submitted(self):
        runner = _Runner([True])
        resp = _post(TestClient(_app(runner)), payload=_payload("warning", status="resolved"))
        assert resp.status_code == 202
        assert resp.json()["ignored_reason"] == "resolved_notification"
        assert runner.submitted == []

    def test_firing_after_resolved_is_still_processed(self):
        runner = _Runner([True])
        client = TestClient(_app(runner))
        _post(client, payload=_payload("warning", status="resolved"))
        resp = _post(client, payload=_payload("warning", status="firing"))
        assert resp.status_code == 202
        assert resp.json().get("deduplicated") is not True
        assert len(runner.submitted) == 1
