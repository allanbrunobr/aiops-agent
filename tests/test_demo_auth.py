"""Regressão F3-4: demo sem auth nos endpoints de mutação + Slack real implícito.

Antes do fix: qualquer POST (start/stop/reset/compare) era aceito sem
credencial, e bastar SLACK_BOT_TOKEN no ambiente para disparar Slack REAL
(dry_run default False), com .env.local auto-carregado.

Depois: AIOPS_DEMO_TOKEN configurado → header X-Demo-Token obrigatório
(comparação constant-time) nos endpoints de mutação; Slack real só com
opt-in explícito AIOPS_DEMO_SLACK_REAL=1 (default dry-run).
"""

from __future__ import annotations

import httpx
import pytest


async def _post(app, path: str, headers: dict | None = None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, headers=headers or {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mutation_without_token_rejected_when_configured(monkeypatch):
    monkeypatch.setenv("AIOPS_DEMO_TOKEN", "secret-demo-token")
    from interfaces.demo.server import app

    resp = await _post(app, "/demo/compare/disable")
    assert resp.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mutation_with_wrong_token_rejected(monkeypatch):
    monkeypatch.setenv("AIOPS_DEMO_TOKEN", "secret-demo-token")
    from interfaces.demo.server import app

    resp = await _post(app, "/demo/compare/disable", {"X-Demo-Token": "wrong-token"})
    assert resp.status_code == 401


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mutation_with_correct_token_accepted(monkeypatch):
    monkeypatch.setenv("AIOPS_DEMO_TOKEN", "secret-demo-token")
    from interfaces.demo.server import app

    resp = await _post(
        app, "/demo/compare/disable", {"X-Demo-Token": "secret-demo-token"}
    )
    assert resp.status_code == 200


@pytest.mark.unit
@pytest.mark.asyncio
async def test_mutation_open_in_dev_mode_without_env_token(monkeypatch):
    """Sem AIOPS_DEMO_TOKEN o demo segue usável em modo dev (bind localhost)."""
    monkeypatch.delenv("AIOPS_DEMO_TOKEN", raising=False)
    from interfaces.demo.server import app

    resp = await _post(app, "/demo/compare/disable")
    assert resp.status_code == 200


@pytest.mark.unit
def test_slack_real_requires_explicit_optin(monkeypatch):
    """Slack real só com token E AIOPS_DEMO_SLACK_REAL=1; default dry-run."""
    from interfaces.demo import server

    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-fake-token")
    monkeypatch.delenv("AIOPS_DEMO_SLACK_REAL", raising=False)
    notifier = server._build_slack_notifier()
    assert notifier is not None
    assert notifier._dry_run is True

    monkeypatch.setenv("AIOPS_DEMO_SLACK_REAL", "1")
    notifier = server._build_slack_notifier()
    assert notifier is not None
    assert notifier._dry_run is False


@pytest.mark.unit
def test_slack_none_without_token(monkeypatch):
    from interfaces.demo import server

    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.setenv("AIOPS_DEMO_SLACK_REAL", "1")
    assert server._build_slack_notifier() is None
