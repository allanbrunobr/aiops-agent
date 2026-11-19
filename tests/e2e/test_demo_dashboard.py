"""Playwright E2E: demo dashboard renders, controls work, live WS frames update.

Run on the CI host (M1):
    .venv/bin/python -m pytest tests/e2e -m e2e --timeout=120

Requires playwright + chromium and internet for the CDN chart assets.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

pytest.importorskip("playwright")
pytestmark = pytest.mark.e2e


def _get_json(url: str) -> object:
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.loads(r.read())


def test_dashboard_loads(dashboard) -> None:
    page, errors = dashboard
    assert page.title() == "AIOps Agent — Live Demo"
    assert page.text_content("#phase-badge").strip() == "IDLE"
    options = page.eval_on_selector_all("#scenario-select option", "els => els.length")
    assert options == 4
    assert page.is_visible("#start-btn")
    assert page.is_visible("#scenario-select")
    assert errors == [], f"console/page errors: {errors}"


def test_start_scenario_streams_live_frames(dashboard, demo_server) -> None:
    page, errors = dashboard
    page.select_option("#scenario-select", "memory-leak")
    page.select_option("#speed-select", "8")
    page.click("#start-btn")

    # Control state flips immediately after the start POST resolves.
    page.wait_for_selector("#stop-btn", state="visible", timeout=15000)
    assert not page.is_visible("#start-btn")

    # The WebSocket tick loop drives the phase badge away from IDLE.
    page.wait_for_function(
        "() => document.getElementById('phase-badge').textContent.trim() !== 'IDLE'",
        timeout=45000,
    )
    phase = page.text_content("#phase-badge").strip()
    assert phase and phase != "IDLE"

    # Live log stream is populated by the broadcast frames.
    page.wait_for_function(
        "() => document.getElementById('log-stream').children.length > 0",
        timeout=30000,
    )
    log_children = page.eval_on_selector_all("#log-stream > *", "els => els.length")
    assert log_children > 0

    status = _get_json(f"{demo_server}/demo/status")
    assert status["is_running"] is True
    assert status["scenario"] == "memory-leak"
    assert errors == [], f"console/page errors: {errors}"


def test_stop_scenario_returns_control(dashboard, demo_server) -> None:
    page, _errors = dashboard
    page.select_option("#scenario-select", "payment-pool-exhaustion")
    page.click("#start-btn")
    page.wait_for_selector("#stop-btn", state="visible", timeout=15000)

    page.click("#stop-btn")
    page.wait_for_selector("#start-btn", state="visible", timeout=15000)
    assert not page.is_visible("#stop-btn")

    status = _get_json(f"{demo_server}/demo/status")
    assert status["is_running"] is False


def test_story_page_loads(page, demo_server) -> None:
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.goto(f"{demo_server}/demo/story", wait_until="domcontentloaded", timeout=30000)
    assert page.title() == "AIOps Agent — Storytelling Demo"
    body_len = page.evaluate("() => document.body.innerText.length")
    assert body_len > 0
    assert errors == [], f"page errors: {errors}"


def test_scenarios_api_lists_four(demo_server) -> None:
    scenarios = _get_json(f"{demo_server}/demo/scenarios")
    assert isinstance(scenarios, list)
    assert len(scenarios) == 4
    names = {s["name"] for s in scenarios}
    assert {
        "payment-pool-exhaustion",
        "payment-pool-recovery",
        "memory-leak",
        "unknown-fault",
    } <= names


def test_postmortem_not_ready_before_resolved(demo_server) -> None:
    # Before any scenario reaches RESOLVED the endpoint contracts to 204.
    with urllib.request.urlopen(f"{demo_server}/demo/postmortem", timeout=10) as r:
        assert r.status == 204
