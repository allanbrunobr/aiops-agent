"""Envia notificacao de teste completa via SlackNotifier com Block Kit.

Simula o ciclo completo: pipeline.started → node.completed (x11) → pipeline.completed
Usa o token real do .env.local.

Usage: python infra/test_slack_notification.py
"""

import asyncio
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Carrega .env.local
env_file = project_root / ".env.local"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

from datetime import datetime, timezone

from adapters.credentials import EnvCredentialProvider
from core.lifecycle import LifecycleEvent
from interfaces.http.notifications.slack import SlackNotifier


def evt(event_type: str, cid: str, node_name: str | None = None, **data) -> LifecycleEvent:
    """Helper para criar LifecycleEvent com timestamp atual."""
    return LifecycleEvent(
        event_type=event_type,
        correlation_id=cid,
        node_name=node_name,
        timestamp=datetime.now(timezone.utc),
        data=data,
    )


async def main():
    channel = os.environ.get("SLACK_CHANNEL", "#aiops-alerts")
    creds = EnvCredentialProvider()

    notifier = SlackNotifier(channel=channel, creds=creds)

    if notifier._client is None:
        print("ERRO: SLACK_BOT_TOKEN nao configurado ou invalido")
        return

    correlation_id = "inc-20260312-demo0001"

    # 1. Pipeline started
    print(f"[1/4] Enviando pipeline.started para {channel}...")
    await notifier.on_event(evt(
        "pipeline.started",
        correlation_id,
        title="Connection Pool Exhaustion - payment-svc",
        severity="critical",
        service="payment-svc",
    ))
    await asyncio.sleep(1.5)

    # 2. Nodes completing progressivamente
    nodes = [
        ("telemetry_sanitizer", 45),
        ("log_parser", 320),
        ("metric_filter", 180),
        ("trace_filter", 95),
        ("context_compressor", 210),
        ("rag_retriever", 450),
        ("root_cause_analyzer", 1200),
        ("pre_execution_validator", 85),
        ("tnr", 120),
        ("version_validator", 60),
        ("postmortem_generator", 380),
    ]

    for i, (node_name, duration_ms) in enumerate(nodes, 1):
        print(f"[2/4] Node {i}/{len(nodes)}: {node_name} ({duration_ms}ms)")
        await notifier.on_event(evt(
            "node.completed",
            correlation_id,
            node_name=node_name,
            duration_ms=duration_ms,
        ))
        await asyncio.sleep(0.8)

    # 3. Pipeline completed com diagnostico
    print("[3/4] Enviando pipeline.completed com diagnostico...")
    await notifier.on_event(LifecycleEvent(
        event_type="pipeline.completed",
        correlation_id=correlation_id,
        node_name=None,
        timestamp=datetime.now(timezone.utc),
        data={
            "root_cause": (
                "Config change in deploy v2.3.1 reduced max_connections from 200 to 50, "
                "causing connection pool exhaustion under normal load (~150 req/s). "
                "Pool saturated at 50/50 connections, new requests queued indefinitely."
            ),
            "evidence": [
                "Deploy event at 14:19 UTC: payment-svc v2.3.1 config change",
                "connection_pool_active = 50/50 (100% saturated) since 14:23",
                "error_rate spiked from 0.5% to 38.2% at 14:23",
                "p95 latency increased from 45ms to 950ms",
                "Drain3 cluster: 'Connection pool exhausted, 80 requests waiting'",
            ],
            "confidence": 0.92,
            "proposed_fix": "kubectl set env deployment/payment-svc MAX_CONNECTIONS=200",
            "risk_level": "LOW",
            "blast_radius": "1 service (payment-svc)",
            "total_duration_ms": 3145,
        },
    ))

    print("[4/4] Notificacao completa enviada!")
    print(f"     Verifique o canal {channel} no Slack.")


if __name__ == "__main__":
    asyncio.run(main())
