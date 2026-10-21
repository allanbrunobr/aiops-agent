"""
Minimal Slack API mock server for local testing.

Accepts the same HTTP API calls as Slack's Web API and stores messages
in memory so integration tests can verify what was sent.

Endpoints mocked:
  POST /api/chat.postMessage   - Post a message to a channel
  POST /api/chat.update        - Update an existing message
  GET  /api/auth.test          - Validate token (always succeeds)
  GET  /_mock/messages          - Retrieve all captured messages (test helper)
  DELETE /_mock/messages        - Clear captured messages (test helper)

Usage:
  python slack_mock_server.py              # Runs on :47892
  SLACK_MOCK_PORT=9999 python slack_mock_server.py

  Then point your Slack SDK at this server:
    from slack_sdk import WebClient
    client = WebClient(token="xoxb-mock-token", base_url="http://localhost:47892/api/")

Docker:
  docker build -t slack-mock -f Dockerfile.slack-mock .
  docker run -p 47892:47892 slack-mock
"""

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

app = FastAPI(title="Slack Mock Server")

# ---------------------------------------------------------------------------
# In-memory message store
# ---------------------------------------------------------------------------

@dataclass
class StoredMessage:
    channel: str
    text: str
    blocks: list[dict] | None
    ts: str
    thread_ts: str | None
    updated: bool = False
    raw_request: dict = field(default_factory=dict)


messages: list[StoredMessage] = []


def _generate_ts() -> str:
    """Generate a Slack-style message timestamp (e.g., '1710252600.000100')."""
    return f"{time.time():.6f}"


# ---------------------------------------------------------------------------
# Slack API endpoints
# ---------------------------------------------------------------------------

@app.post("/api/chat.postMessage")
async def chat_post_message(request: Request) -> JSONResponse:
    """Mock chat.postMessage -- stores the message and returns success."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)
        # blocks may be JSON-encoded string in form data
        if "blocks" in body and isinstance(body["blocks"], str):
            body["blocks"] = json.loads(body["blocks"])

    ts = _generate_ts()
    channel = body.get("channel", "unknown")
    text = body.get("text", "")
    blocks = body.get("blocks")
    thread_ts = body.get("thread_ts")

    msg = StoredMessage(
        channel=channel,
        text=text,
        blocks=blocks,
        ts=ts,
        thread_ts=thread_ts,
        raw_request=body,
    )
    messages.append(msg)
    print(f"[chat.postMessage] channel={channel} text={text[:80]!r}")

    return JSONResponse({
        "ok": True,
        "channel": channel,
        "ts": ts,
        "message": {
            "text": text,
            "ts": ts,
            "type": "message",
            "subtype": "bot_message",
            "bot_id": "B_MOCK_BOT",
        },
    })


@app.post("/api/chat.update")
async def chat_update(request: Request) -> JSONResponse:
    """Mock chat.update -- finds and updates a stored message."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)
        if "blocks" in body and isinstance(body["blocks"], str):
            body["blocks"] = json.loads(body["blocks"])

    channel = body.get("channel", "unknown")
    ts = body.get("ts", "")
    text = body.get("text", "")
    blocks = body.get("blocks")

    # Find and update the original message
    found = False
    for msg in messages:
        if msg.ts == ts and msg.channel == channel:
            msg.text = text
            msg.blocks = blocks
            msg.updated = True
            msg.raw_request = body
            found = True
            break

    if not found:
        # Still return success (Slack does too if ts is wrong, just no-ops)
        print(f"[chat.update] WARNING: message ts={ts} not found, storing as new")
        messages.append(StoredMessage(
            channel=channel, text=text, blocks=blocks,
            ts=ts, thread_ts=None, updated=True, raw_request=body,
        ))

    print(f"[chat.update] channel={channel} ts={ts} text={text[:80]!r}")

    return JSONResponse({
        "ok": True,
        "channel": channel,
        "ts": ts,
        "text": text,
    })


@app.get("/api/auth.test")
@app.post("/api/auth.test")
async def auth_test(request: Request) -> JSONResponse:
    """Mock auth.test -- always returns success."""
    return JSONResponse({
        "ok": True,
        "url": "https://mock-workspace.slack.com/",
        "team": "Mock Workspace",
        "user": "aiops-bot",
        "team_id": "T_MOCK",
        "user_id": "U_MOCK",
        "bot_id": "B_MOCK_BOT",
    })


# ---------------------------------------------------------------------------
# Test helper endpoints (not part of Slack API)
# ---------------------------------------------------------------------------

@app.get("/_mock/messages")
async def get_messages() -> JSONResponse:
    """Return all captured messages for test assertions."""
    return JSONResponse({
        "count": len(messages),
        "messages": [asdict(m) for m in messages],
    })


@app.delete("/_mock/messages")
async def clear_messages() -> JSONResponse:
    """Clear all captured messages."""
    messages.clear()
    return JSONResponse({"ok": True, "cleared": True})


@app.get("/_mock/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "messages_captured": len(messages)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("SLACK_MOCK_PORT", "47892"))
    print(f"Slack Mock Server starting on :{port}")
    print(f"Point your Slack SDK at: http://localhost:{port}/api/")
    uvicorn.run(app, host="0.0.0.0", port=port)
