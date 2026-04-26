"""Launcher desktop — sobe demo/server.py e abre janela nativa com loading."""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request

import webview

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_URL = "http://localhost:47891"
DASHBOARD_URL = f"{SERVER_URL}/demo/"
LOG_PATH = os.path.join(PROJECT_ROOT, "demo", "desktop_app.log")


def _start_server() -> subprocess.Popen:
    """Sobe demo/server.py como subprocesso com log em arquivo."""
    log_file = open(LOG_PATH, "w")
    return subprocess.Popen(
        [sys.executable, os.path.join(PROJECT_ROOT, "demo", "server.py")],
        cwd=PROJECT_ROOT,
        stdout=log_file,
        stderr=log_file,
    )


def _wait_for_server(max_seconds: int = 30) -> bool:
    """Bloqueia até o server responder ou timeout."""
    for _ in range(max_seconds * 2):
        try:
            urllib.request.urlopen(DASHBOARD_URL, timeout=2)
            return True
        except Exception:
            time.sleep(0.5)
    return False


def _cleanup(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> None:
    server_proc = _start_server()

    if not _wait_for_server():
        _cleanup(server_proc)
        print(f"Server não iniciou. Veja {LOG_PATH}")
        sys.exit(1)

    window = webview.create_window(
        "AIOps Dashboard",
        url=DASHBOARD_URL,
        width=1360,
        height=860,
        min_size=(800, 600),
    )

    window.events.closed += lambda: _cleanup(server_proc)

    try:
        webview.start()
    finally:
        _cleanup(server_proc)


if __name__ == "__main__":
    main()
