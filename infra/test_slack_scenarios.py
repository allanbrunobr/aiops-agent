"""Envia multiplos cenarios de incidentes distintos para o Slack.

Cada cenario simula um problema diferente com root cause, evidencias e fix proprios.
Os incidentes aparecem como mensagens separadas no canal, simulando um dia agitado de oncall.

Usage: python infra/test_slack_scenarios.py
       python infra/test_slack_scenarios.py --scenario 3     # roda apenas o cenario 3
       python infra/test_slack_scenarios.py --fast            # sem delays entre nodes
"""

import argparse
import asyncio
import os
import sys
import uuid
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

from core.credentials import EnvCredentialProvider
from core.lifecycle import LifecycleEvent
from server.notifications.slack import SlackNotifier


# ---------------------------------------------------------------------------
# Cenarios de incidentes
# ---------------------------------------------------------------------------

SCENARIOS = [
    # 1. OOM Kill — Kubernetes
    {
        "title": "OOMKilled: order-processing-worker",
        "severity": "critical",
        "service": "order-processing",
        "node_durations": {
            "telemetry_sanitizer": 38,
            "log_parser": 280,
            "metric_filter": 195,
            "trace_filter": 110,
            "context_compressor": 175,
            "rag_retriever": 520,
            "root_cause_analyzer": 1850,
            "pre_execution_validator": 70,
            "tnr": 95,
            "version_validator": 55,
            "postmortem_generator": 410,
        },
        "diagnosis": {
            "root_cause": (
                "Memory leak in order-processing-worker caused by unbounded "
                "in-memory cache of deserialized Protobuf messages. Container "
                "hit 2Gi memory limit and was OOMKilled by Kubernetes. "
                "Leak introduced in commit abc123f (v3.8.0) — missing cache "
                "eviction policy on OrderCache.warm_up()."
            ),
            "evidence": [
                "kubectl events: OOMKilled on pod order-processing-worker-7b4d9 at 09:12 UTC",
                "container_memory_rss grew linearly from 400Mi to 2048Mi over 47 minutes",
                "Heap dump: OrderCache holding 1.2M entries (expected max: 10K)",
                "No cache TTL or max-size configured in OrderCache since v3.8.0",
                "Restart loop: 4 OOMKills in 20 minutes (CrashLoopBackOff)",
            ],
            "confidence": 0.88,
            "proposed_fix": (
                "kubectl set resources deployment/order-processing-worker "
                "--limits=memory=4Gi && kubectl rollout restart deployment/order-processing-worker"
            ),
            "risk_level": "MEDIUM",
            "blast_radius": "1 deployment (order-processing-worker), 3 pods",
            "total_duration_ms": 3798,
        },
    },

    # 2. Certificate Expiration — TLS
    {
        "title": "TLS Certificate Expired: api-gateway",
        "severity": "critical",
        "service": "api-gateway",
        "node_durations": {
            "telemetry_sanitizer": 32,
            "log_parser": 150,
            "metric_filter": 90,
            "trace_filter": 65,
            "context_compressor": 120,
            "rag_retriever": 380,
            "root_cause_analyzer": 950,
            "pre_execution_validator": 60,
            "tnr": 80,
            "version_validator": 45,
            "postmortem_generator": 290,
        },
        "diagnosis": {
            "root_cause": (
                "TLS certificate for *.api.company.com expired at 2026-03-12T00:00:00Z. "
                "cert-manager renewal failed silently 14 days ago due to DNS-01 challenge "
                "timeout (Route53 API rate-limited). All HTTPS traffic to api-gateway "
                "returns ERR_CERT_DATE_INVALID. 100% of external API calls failing."
            ),
            "evidence": [
                "openssl s_client: certificate expired on Mar 12 00:00:00 2026 GMT",
                "cert-manager logs: ACME DNS-01 challenge timeout (14 days ago)",
                "Route53 CloudTrail: ThrottlingException on ChangeResourceRecordSets",
                "error_rate = 100% on api-gateway since 00:00 UTC",
                "Downstream services unaffected (mTLS uses separate cert chain)",
            ],
            "confidence": 0.97,
            "proposed_fix": (
                "kubectl cert-manager renew api-gateway-tls -n ingress && "
                "kubectl rollout restart deployment/api-gateway -n ingress"
            ),
            "risk_level": "LOW",
            "blast_radius": "All external API traffic (internal unaffected)",
            "total_duration_ms": 2262,
        },
    },

    # 3. Database Deadlock — PostgreSQL
    {
        "title": "Deadlock Storm: inventory-db (PostgreSQL)",
        "severity": "warning",
        "service": "inventory-svc",
        "node_durations": {
            "telemetry_sanitizer": 41,
            "log_parser": 450,
            "metric_filter": 220,
            "trace_filter": 180,
            "context_compressor": 260,
            "rag_retriever": 610,
            "root_cause_analyzer": 2100,
            "pre_execution_validator": 90,
            "tnr": 130,
            "version_validator": 70,
            "postmortem_generator": 350,
        },
        "diagnosis": {
            "root_cause": (
                "Concurrent inventory reservation queries causing deadlock chains "
                "in PostgreSQL. Two transactions acquiring row-level locks in "
                "opposite order on inventory_items and reservation_holds tables. "
                "Triggered by new bulk-reservation feature (v2.1.0) that doesn't "
                "enforce consistent lock ordering. Rate: ~12 deadlocks/min."
            ),
            "evidence": [
                "pg_stat_activity: 12 deadlocks/min detected since 11:45 UTC",
                "PostgreSQL log: 'ERROR: deadlock detected' on inventory_items + reservation_holds",
                "Trace analysis: bulk_reserve() acquires locks in (items→holds), single_reserve() in (holds→items)",
                "Feature flag bulk_reservations enabled at 11:40 UTC (v2.1.0 deploy)",
                "p95 latency for /api/inventory/reserve: 45ms → 3200ms",
            ],
            "confidence": 0.85,
            "proposed_fix": (
                "kubectl set env deployment/inventory-svc FEATURE_BULK_RESERVATIONS=false"
            ),
            "risk_level": "LOW",
            "blast_radius": "inventory-svc write path (reads unaffected)",
            "total_duration_ms": 4501,
        },
    },

    # 4. DNS Resolution Failure — Intermittent
    {
        "title": "Intermittent DNS Failures: auth-service",
        "severity": "warning",
        "service": "auth-service",
        "node_durations": {
            "telemetry_sanitizer": 35,
            "log_parser": 390,
            "metric_filter": 160,
            "trace_filter": 140,
            "context_compressor": 200,
            "rag_retriever": 480,
            "root_cause_analyzer": 1650,
            "pre_execution_validator": 75,
            "tnr": 110,
            "version_validator": 50,
            "postmortem_generator": 320,
        },
        "diagnosis": {
            "root_cause": (
                "CoreDNS pods experiencing cache corruption after update to v1.11.3. "
                "Intermittent SERVFAIL responses for redis-cluster.internal.svc.cluster.local "
                "causing auth-service session validation failures. Affects ~15% of DNS queries. "
                "Known issue: CoreDNS #6234 — race condition in negative cache invalidation."
            ),
            "evidence": [
                "auth-service logs: 847 'SERVFAIL resolving redis-cluster.internal' in 30 min",
                "CoreDNS metrics: cache_misses_total spiked 3x after pod restart at 08:30",
                "tcpdump: SERVFAIL responses correlate with CoreDNS pod coredns-7f89b (node-3)",
                "Upstream issue: CoreDNS GitHub #6234 confirmed in v1.11.3",
                "Session validation error_rate: 0.2% → 15.3% (auth-service only)",
            ],
            "confidence": 0.78,
            "proposed_fix": (
                "kubectl rollout restart deployment/coredns -n kube-system && "
                "kubectl set image deployment/coredns coredns=coredns/coredns:1.11.1 -n kube-system"
            ),
            "risk_level": "MEDIUM",
            "blast_radius": "All services resolving DNS via CoreDNS (cluster-wide)",
            "total_duration_ms": 3610,
        },
    },

    # 5. Kafka Consumer Lag — Data Pipeline
    {
        "title": "Consumer Lag Critical: events-pipeline",
        "severity": "critical",
        "service": "events-pipeline",
        "node_durations": {
            "telemetry_sanitizer": 42,
            "log_parser": 510,
            "metric_filter": 240,
            "trace_filter": 85,
            "context_compressor": 190,
            "rag_retriever": 560,
            "root_cause_analyzer": 1400,
            "pre_execution_validator": 80,
            "tnr": 100,
            "version_validator": 65,
            "postmortem_generator": 340,
        },
        "diagnosis": {
            "root_cause": (
                "Kafka consumer group 'events-processor' lag reached 2.4M messages "
                "on topic 'user-events' (partition 7). Caused by a poison pill message "
                "with malformed JSON that triggers infinite deserialization retry loop. "
                "Consumer thread blocked on partition 7 since 06:15 UTC; other partitions "
                "processing normally."
            ),
            "evidence": [
                "kafka-consumer-groups: lag=2,412,890 on user-events:7 (other partitions: lag<100)",
                "Consumer logs: 'DeserializationException: Unexpected character at offset 847' repeating every 500ms",
                "Message offset 4812903 on partition 7: contains truncated JSON (network split during produce)",
                "Producer logs: timeout on partition 7 leader at 06:14 UTC (broker-3 rebalance)",
                "events-pipeline throughput: 45K/s → 38K/s (partition 7 stuck = ~15% capacity loss)",
            ],
            "confidence": 0.91,
            "proposed_fix": (
                "kafka-consumer-groups --bootstrap-server kafka:9092 "
                "--group events-processor --topic user-events --reset-offsets "
                "--to-offset 4812904 --partition 7 --execute"
            ),
            "risk_level": "LOW",
            "blast_radius": "1 partition of user-events topic (1 message skipped)",
            "total_duration_ms": 3612,
        },
    },

    # 6. Pipeline Failure — node falha no meio
    {
        "title": "Disk Full: logging-collector (EBS Volume)",
        "severity": "critical",
        "service": "logging-collector",
        "node_durations": {
            "telemetry_sanitizer": 30,
            "log_parser": 280,
            "metric_filter": 150,
            "trace_filter": 70,
            "context_compressor": 190,
            "rag_retriever": 420,
            "root_cause_analyzer": None,  # FALHA aqui
        },
        "failure": {
            "failed_node": "root_cause_analyzer",
            "error": (
                "RAG retriever returned 0 results — FAISS index corrupted. "
                "Unable to complete root cause analysis without historical context. "
                "Falling back to heuristic-only mode."
            ),
        },
        "diagnosis": None,  # pipeline falhou
    },
]


def evt(event_type: str, cid: str, node_name: str | None = None, **data) -> LifecycleEvent:
    return LifecycleEvent(
        event_type=event_type,
        correlation_id=cid,
        node_name=node_name,
        timestamp=datetime.now(timezone.utc),
        data=data,
    )


async def run_scenario(notifier: SlackNotifier, scenario: dict, index: int, fast: bool = False):
    """Executa um cenario completo de incidente."""
    cid = f"inc-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    delay = 0.3 if fast else 0.8

    print(f"\n{'='*60}")
    print(f"  Cenario {index}: {scenario['title']}")
    print(f"  Severity: {scenario['severity']} | Service: {scenario['service']}")
    print(f"  Correlation ID: {cid}")
    print(f"{'='*60}")

    # 1. Pipeline started
    print(f"  [started] Enviando pipeline.started...")
    await notifier.on_event(evt(
        "pipeline.started",
        cid,
        title=scenario["title"],
        severity=scenario["severity"],
        service=scenario["service"],
    ))
    await asyncio.sleep(0.5 if fast else 1.5)

    # 2. Nodes
    is_failure = scenario.get("failure") is not None
    node_durations = scenario["node_durations"]

    for node_name, duration_ms in node_durations.items():
        if duration_ms is None:
            # Node que falha
            print(f"  [FAILED]  {node_name} — pipeline falhou!")
            await notifier.on_event(evt(
                "pipeline.failed",
                cid,
                node_name=node_name,
                error=scenario["failure"]["error"],
            ))
            return cid

        print(f"  [node]    {node_name} ({duration_ms}ms)")
        await notifier.on_event(evt(
            "node.completed",
            cid,
            node_name=node_name,
            duration_ms=duration_ms,
        ))
        await asyncio.sleep(delay)

    # 3. Nodes restantes se nao falhou (safety nodes que nao estao no dict)
    remaining_nodes = {
        "pre_execution_validator": 85,
        "tnr": 120,
        "version_validator": 60,
        "postmortem_generator": 380,
    }
    for node_name, default_dur in remaining_nodes.items():
        if node_name not in node_durations:
            dur = default_dur
            print(f"  [node]    {node_name} ({dur}ms)")
            await notifier.on_event(evt(
                "node.completed", cid, node_name=node_name, duration_ms=dur,
            ))
            await asyncio.sleep(delay)

    # 4. Pipeline completed
    if scenario["diagnosis"]:
        print(f"  [done]    Enviando diagnostico final...")
        await notifier.on_event(LifecycleEvent(
            event_type="pipeline.completed",
            correlation_id=cid,
            node_name=None,
            timestamp=datetime.now(timezone.utc),
            data=scenario["diagnosis"],
        ))

    return cid


async def main():
    parser = argparse.ArgumentParser(description="Envia cenarios de incidentes para o Slack")
    parser.add_argument("--scenario", type=int, help="Numero do cenario (1-6). Sem arg = todos.")
    parser.add_argument("--fast", action="store_true", help="Delays reduzidos entre nodes")
    args = parser.parse_args()

    channel = os.environ.get("SLACK_CHANNEL", "#aiops-alerts")
    creds = EnvCredentialProvider()
    notifier = SlackNotifier(channel=channel, creds=creds)

    if notifier._client is None:
        print("ERRO: SLACK_BOT_TOKEN nao configurado. Edite .env.local")
        return

    print(f"Canal: {channel}")
    print(f"Cenarios disponiveis: {len(SCENARIOS)}")

    if args.scenario:
        if args.scenario < 1 or args.scenario > len(SCENARIOS):
            print(f"ERRO: cenario deve ser entre 1 e {len(SCENARIOS)}")
            return
        scenarios_to_run = [(args.scenario, SCENARIOS[args.scenario - 1])]
    else:
        scenarios_to_run = list(enumerate(SCENARIOS, 1))

    results = []
    for idx, scenario in scenarios_to_run:
        cid = await run_scenario(notifier, scenario, idx, fast=args.fast)
        results.append((idx, scenario["title"], cid))
        if idx < len(scenarios_to_run):
            pause = 1 if args.fast else 3
            print(f"\n  Aguardando {pause}s antes do proximo cenario...")
            await asyncio.sleep(pause)

    print(f"\n{'='*60}")
    print(f"  Todos os cenarios enviados!")
    print(f"{'='*60}")
    for idx, title, cid in results:
        print(f"  {idx}. {title}")
        print(f"     {cid}")
    print(f"\n  Verifique o canal {channel} no Slack.")


if __name__ == "__main__":
    asyncio.run(main())
