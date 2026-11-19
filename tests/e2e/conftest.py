"""Fixtures for the Playwright end-to-end suite of the demo dashboard.

The demo server (``interfaces/demo/server.py``) is started as a real subprocess
on an ephemeral port so the browser exercises the genuine HTTP + WebSocket
stack. Everything runs headless on the CI host (M1); nothing heavy runs on M5.

These tests are marked ``e2e`` and are excluded from the ``unit`` and
``integration or snapshot`` CI jobs. They require ``playwright`` + chromium
(``python -m playwright install chromium``) and internet access for the CDN
chart assets (uPlot / ECharts / GSAP) referenced by ``index.html``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SERVER = REPO / "interfaces" / "demo" / "server.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_ready(base_url: str, timeout: float = 40.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"{base_url}/demo/scenarios", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.3)
    raise RuntimeError(f"demo server not ready at {base_url}")


def _start_server(port: int, extra_env: dict[str, str]) -> subprocess.Popen:
    env = dict(os.environ)
    env.pop("AIOPS_DEMO_TOKEN", None)
    env.pop("ANTHROPIC_API_KEY", None)  # deterministic, no LLM calls
    env["PYTHONPATH"] = str(REPO)
    env.update(extra_env)
    return subprocess.Popen(
        [sys.executable, str(SERVER), "--port", str(port), "--host", "127.0.0.1"],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture
def demo_server() -> Iterator[str]:
    """Demo server in open dev mode (no AIOPS_DEMO_TOKEN). Yields base URL."""
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc = _start_server(port, {})
    try:
        _wait_ready(base_url)
        yield base_url
    finally:
        _stop_server(proc)


@pytest.fixture
def demo_server_with_token() -> Iterator[tuple[str, str]]:
    """Demo server with AIOPS_DEMO_TOKEN set. Yields (base URL, token)."""
    token = "e2e-demo-token-secret-32bytes-min!!"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    proc = _start_server(port, {"AIOPS_DEMO_TOKEN": token})
    try:
        _wait_ready(base_url)
        yield base_url, token
    finally:
        _stop_server(proc)


def collect_page_errors(page) -> list[str]:
    """Attach console-error / pageerror listeners; return the accumulating list."""
    errors: list[str] = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    return errors


def wait_for_chart_libs(page, timeout: float = 30000) -> None:
    """Block until the CDN chart libraries are present (initCharts needs them)."""
    page.wait_for_function(
        "() => typeof window.echarts !== 'undefined' "
        "&& typeof window.uPlot !== 'undefined' "
        "&& typeof window.gsap !== 'undefined'",
        timeout=timeout,
    )


@pytest.fixture
def dashboard(page, demo_server) -> Iterator[tuple[object, list[str]]]:
    """Open the dashboard, wait for chart libs, yield (page, error_list)."""
    errors = collect_page_errors(page)
    page.goto(f"{demo_server}/demo/", wait_until="networkidle", timeout=45000)
    wait_for_chart_libs(page)
    yield page, errors
