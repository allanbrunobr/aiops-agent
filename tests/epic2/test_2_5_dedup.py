"""Testes para Story 2.5: Alert Deduplication."""
from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from core.models import NormalizedAlert
from server.webhook.dedup import DedupStore, generate_fingerprint


def _make_alert(**overrides) -> NormalizedAlert:
    """Helper para criar NormalizedAlert de teste."""
    defaults = {
        "source": "pagerduty",
        "source_id": "P123",
        "correlation_id": "inc-20260308-abc12345",
        "title": "High CPU",
        "description": "CPU above 90%",
        "severity": "critical",
        "alert_type": "incident.triggered",
        "timestamp": datetime.now(timezone.utc),
        "detection_timestamp": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return NormalizedAlert(**defaults)


@pytest.fixture
def dedup_store():
    store = DedupStore()
    yield store
    store.clear()


def test_first_alert_not_duplicate(dedup_store):
    """AC1: primeiro alerta nao e duplicata."""
    alert = _make_alert()
    fp = generate_fingerprint(alert)
    is_dup, _ = dedup_store.is_duplicate(fp)
    assert is_dup is False


def test_same_alert_within_window_is_duplicate(dedup_store):
    """AC1: mesmo alerta dentro da janela e duplicata."""
    alert = _make_alert()
    fp = generate_fingerprint(alert)

    dedup_store.record(fp, "inc-original", ttl_seconds=300)
    is_dup, original_cid = dedup_store.is_duplicate(fp)

    assert is_dup is True
    assert original_cid == "inc-original"


def test_same_alert_after_window_not_duplicate(dedup_store, monkeypatch):
    """AC2: mesmo alerta apos expirar a janela nao e duplicata."""
    alert = _make_alert()
    fp = generate_fingerprint(alert)

    dedup_store.record(fp, "inc-original", ttl_seconds=1)

    # Avanca o tempo alem do TTL
    original_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: original_monotonic() + 2)

    is_dup, _ = dedup_store.is_duplicate(fp)
    assert is_dup is False


def test_different_alerts_not_duplicate(dedup_store):
    """AC: alertas diferentes nao sao deduplicados."""
    alert1 = _make_alert(title="High CPU")
    alert2 = _make_alert(title="High Memory")

    fp1 = generate_fingerprint(alert1)
    fp2 = generate_fingerprint(alert2)

    dedup_store.record(fp1, "inc-1", ttl_seconds=300)
    is_dup, _ = dedup_store.is_duplicate(fp2)
    assert is_dup is False


def test_fingerprint_deterministic():
    """AC: fingerprint e deterministico para mesmo input."""
    alert = _make_alert()
    fp1 = generate_fingerprint(alert)
    fp2 = generate_fingerprint(alert)
    assert fp1 == fp2
    assert len(fp1) == 64  # SHA-256 hex digest


def test_fingerprint_different_for_different_alerts():
    """AC: alertas diferentes produzem fingerprints diferentes."""
    alert1 = _make_alert(title="Alert A")
    alert2 = _make_alert(title="Alert B")
    assert generate_fingerprint(alert1) != generate_fingerprint(alert2)


def test_fingerprint_sha256_length():
    """AC3: fingerprint usa SHA-256 (64 chars hex)."""
    fp = generate_fingerprint(_make_alert())
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_dedup_store_clear(dedup_store):
    """AC3: clear() limpa o store para uso em testes."""
    alert = _make_alert()
    fp = generate_fingerprint(alert)
    dedup_store.record(fp, "inc-1", ttl_seconds=300)
    assert dedup_store.size == 1

    dedup_store.clear()
    assert dedup_store.size == 0


def test_dedup_store_expired_entries_cleanup(dedup_store, monkeypatch):
    """AC: entradas expiradas sao limpas periodicamente."""
    alert = _make_alert()
    fp = generate_fingerprint(alert)
    dedup_store.record(fp, "inc-1", ttl_seconds=1)

    # Avanca tempo alem do cleanup interval (60s) e TTL
    original_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: original_monotonic() + 120)

    # is_duplicate deve limpar entrada expirada
    is_dup, _ = dedup_store.is_duplicate(fp)
    assert is_dup is False
