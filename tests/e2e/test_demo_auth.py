"""E2E (API-level): demo mutation endpoints require X-Demo-Token when configured.

Validates ``require_demo_token`` (interfaces/demo/server.py): with
AIOPS_DEMO_TOKEN set, mutations are 401 without/with-wrong token and succeed
with the correct token; read endpoints stay open. No browser needed.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

pytestmark = pytest.mark.e2e


def _post(url: str, token: str | None = None) -> int:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Demo-Token"] = token
    req = urllib.request.Request(url, data=b"{}", headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return int(r.status)
    except urllib.error.HTTPError as e:
        return int(e.code)


def test_mutation_without_token_is_401(demo_server_with_token) -> None:
    base_url, _token = demo_server_with_token
    assert _post(f"{base_url}/demo/start/memory-leak") == 401
    assert _post(f"{base_url}/demo/stop") == 401
    assert _post(f"{base_url}/demo/reset") == 401


def test_mutation_with_wrong_token_is_401(demo_server_with_token) -> None:
    base_url, _token = demo_server_with_token
    assert _post(f"{base_url}/demo/start/memory-leak", token="wrong-token") == 401


def test_mutation_with_correct_token_succeeds(demo_server_with_token) -> None:
    base_url, token = demo_server_with_token
    assert _post(f"{base_url}/demo/start/memory-leak", token=token) == 200
    assert _post(f"{base_url}/demo/stop", token=token) == 200


def test_read_endpoints_stay_open(demo_server_with_token) -> None:
    base_url, _token = demo_server_with_token
    with urllib.request.urlopen(f"{base_url}/demo/scenarios", timeout=10) as r:
        assert r.status == 200
    with urllib.request.urlopen(f"{base_url}/demo/status", timeout=10) as r:
        assert r.status == 200
