"""Cenarios focados em diagnostico baseado em LOGS e TRACES.

Cada cenario demonstra como o pipeline correlaciona sinais de diferentes fontes
(logs com erros HTTP, traces com bottlenecks, metricas anomalas) para chegar
na causa raiz.

Usage: python infra/test_slack_log_trace_scenarios.py
       python infra/test_slack_log_trace_scenarios.py --scenario 2
       python infra/test_slack_log_trace_scenarios.py --fast
"""

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

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


SCENARIOS = [
    # -------------------------------------------------------------------------
    # 1. HTTP 500 Cascade — um servico upstream falha e espalha 500s
    # -------------------------------------------------------------------------
    {
        "title": "HTTP 500 Cascade: user-api → auth-service → Redis",
        "severity": "critical",
        "service": "user-api",
        "node_durations": {
            "telemetry_sanitizer": 35,
            "log_parser": 480,
            "metric_filter": 195,
            "trace_filter": 220,
            "context_compressor": 180,
            "rag_retriever": 540,
            "root_cause_analyzer": 1750,
            "pre_execution_validator": 72,
            "tnr": 105,
            "version_validator": 58,
            "postmortem_generator": 395,
        },
        "diagnosis": {
            "root_cause": (
                "Redis Cluster node redis-7 elected as new primary after failover, "
                "but auth-service connection pool held stale connections to the old primary. "
                "auth-service returned HTTP 500 on /validate-token for all requests. "
                "user-api propagated 500s upstream (no circuit breaker configured). "
                "Drain3 template: 'ETIMEDOUT connecting to redis-7.redis-cluster:6379' x1,847 in 3 min."
            ),
            "evidence": [
                "[LOG][auth-service] ERROR 'RedisConnectionError: ETIMEDOUT redis-7.redis-cluster:6379' x1,847 (10:15:02→10:18:14) relevance=11.8",
                "[LOG][auth-service] ERROR 'HTTP 500 on POST /validate-token — upstream dependency failed' x2,340 (10:15:05→10:18:20) relevance=10.2",
                "[LOG][user-api] ERROR 'HTTP 500 from auth-service on /validate-token, forwarding 500 to client' x2,105 (10:15:06→10:18:20) relevance=9.5",
                "[LOG][redis-cluster] WARN 'Failover complete: redis-7 promoted to primary, redis-3 demoted' at 10:14:58 relevance=8.0",
                "[TRACE] client → user-api → auth-service → redis: 30,200ms (normal 12ms) | bottleneck: redis (ETIMEDOUT after 30s) | redis.connected=false",
                "[METRIC] auth-service/error_rate: spike from 0.1% to 98.7% at 10:15 (247σ) | user-api/error_rate: 0.3% → 87.5% at 10:15 (198σ)",
                "[CORRELATION] Log-Metric match: redis failover log at 10:14:58 precedes auth-service error spike at 10:15:02 (4s delay = connection timeout)",
            ],
            "confidence": 0.94,
            "proposed_fix": (
                "kubectl rollout restart deployment/auth-service -n production && "
                "kubectl exec -it auth-service-pod -- redis-cli -c CLUSTER RESET SOFT"
            ),
            "risk_level": "LOW",
            "blast_radius": "auth-service + user-api (all authenticated endpoints)",
            "total_duration_ms": 4030,
        },
    },

    # -------------------------------------------------------------------------
    # 2. HTTP 401 Storm — token validation failing after secret rotation
    # -------------------------------------------------------------------------
    {
        "title": "HTTP 401 Unauthorized Storm: All Services",
        "severity": "critical",
        "service": "api-gateway",
        "node_durations": {
            "telemetry_sanitizer": 30,
            "log_parser": 520,
            "metric_filter": 170,
            "trace_filter": 190,
            "context_compressor": 210,
            "rag_retriever": 490,
            "root_cause_analyzer": 1950,
            "pre_execution_validator": 68,
            "tnr": 98,
            "version_validator": 52,
            "postmortem_generator": 360,
        },
        "diagnosis": {
            "root_cause": (
                "JWT signing key rotated via Vault at 14:30 UTC but only api-gateway "
                "picked up the new key (hot-reload enabled). Downstream services "
                "(user-svc, order-svc, billing-svc) still validate against the OLD key. "
                "All inter-service JWT tokens signed with new key are rejected as invalid "
                "by downstream services → HTTP 401 on every authenticated call. "
                "Drain3 template: 'JWT signature verification failed: kid=vault-key-2026-03-12 not found' x5,230."
            ),
            "evidence": [
                "[LOG][api-gateway] INFO 'Vault secret rotation detected, JWT signing key updated to kid=vault-key-2026-03-12' at 14:30:01 relevance=9.0",
                "[LOG][user-svc] ERROR 'JWT signature verification failed: kid=vault-key-2026-03-12 not found in local keystore' x1,847 (14:30:05→14:45:00) relevance=12.5",
                "[LOG][order-svc] ERROR 'HTTP 401 Unauthorized — token validation failed' x1,623 (14:30:06→14:45:00) relevance=11.0",
                "[LOG][billing-svc] ERROR 'JWT signature verification failed: kid=vault-key-2026-03-12 not found' x1,760 (14:30:08→14:45:00) relevance=12.5",
                "[TRACE] client → api-gateway → user-svc: 8ms | user-svc returned 401 | jwt.kid=vault-key-2026-03-12 | jwt.validation=FAILED",
                "[METRIC] user-svc/http_401_count: 0 → 1,847 in 15min | order-svc/http_401_count: 0 → 1,623 | api-gateway/http_401_count: 0 (gateway itself OK)",
                "[CORRELATION] Timeline: Vault rotation at 14:30:01 → gateway key update at 14:30:01 → first 401 at 14:30:05 (4s = downstream request with new token)",
            ],
            "confidence": 0.96,
            "proposed_fix": (
                "kubectl rollout restart deployment/user-svc deployment/order-svc deployment/billing-svc -n production"
            ),
            "risk_level": "LOW",
            "blast_radius": "All authenticated inter-service calls (user-svc, order-svc, billing-svc). api-gateway and public auth unaffected.",
            "total_duration_ms": 4138,
        },
    },

    # -------------------------------------------------------------------------
    # 3. Slow Trace Chain — latencia cascateando por 4 servicos
    # -------------------------------------------------------------------------
    {
        "title": "Latency Cascade: checkout-flow (p95: 12s → normal 200ms)",
        "severity": "warning",
        "service": "checkout-svc",
        "node_durations": {
            "telemetry_sanitizer": 38,
            "log_parser": 340,
            "metric_filter": 250,
            "trace_filter": 310,
            "context_compressor": 195,
            "rag_retriever": 580,
            "root_cause_analyzer": 2200,
            "pre_execution_validator": 85,
            "tnr": 115,
            "version_validator": 62,
            "postmortem_generator": 405,
        },
        "diagnosis": {
            "root_cause": (
                "pricing-engine executing N+1 query pattern after v4.2.0 deploy. "
                "For each item in cart, a separate SQL query fetches discount rules "
                "instead of batch query. Cart with 50 items → 50 sequential DB queries "
                "(~200ms each) = 10s total. Trace shows pricing-engine span at 10,200ms "
                "(normal: 45ms). No errors logged — only latency degradation visible in traces."
            ),
            "evidence": [
                "[TRACE] client → api-gateway → checkout-svc → pricing-engine → postgres: 12,400ms (normal 200ms) | bottleneck: pricing-engine (10,200ms, normal 45ms)",
                "[TRACE] pricing-engine internal spans: 50 sequential 'SELECT discount_rules WHERE product_id=?' spans, ~200ms each | db.query_count=50 per request",
                "[LOG][pricing-engine] DEBUG 'Fetching discount rules for product_id=X' x50 per request (no ERROR logs — silent performance regression) relevance=3.0",
                "[LOG][checkout-svc] WARN 'Upstream pricing-engine response time 10,200ms exceeds SLO (500ms)' x342 (16:00→16:30) relevance=7.5",
                "[METRIC] pricing-engine/latency_p95: 45ms → 10,200ms at 16:00 (225σ) | checkout-svc/latency_p95: 200ms → 12,400ms (61σ)",
                "[METRIC] pricing-engine/db_query_count_per_request: 1 → 50 at 16:00 (deploy v4.2.0) | postgres/active_connections: 20 → 180 (approaching pool limit 200)",
                "[CORRELATION] Deploy event at 15:58 (v4.2.0) → latency spike at 16:00 (2 min lag = first requests hitting new pods)",
            ],
            "confidence": 0.89,
            "proposed_fix": (
                "kubectl rollout undo deployment/pricing-engine -n production"
            ),
            "risk_level": "LOW",
            "blast_radius": "checkout-svc (checkout flow only, browse/search unaffected)",
            "total_duration_ms": 4580,
        },
    },

    # -------------------------------------------------------------------------
    # 4. Log Pattern sem metricas — erro intermitente visivel apenas em logs
    # -------------------------------------------------------------------------
    {
        "title": "Silent Data Corruption: notification-worker",
        "severity": "warning",
        "service": "notification-worker",
        "node_durations": {
            "telemetry_sanitizer": 42,
            "log_parser": 610,
            "metric_filter": 130,
            "trace_filter": 95,
            "context_compressor": 240,
            "rag_retriever": 650,
            "root_cause_analyzer": 2400,
            "pre_execution_validator": 78,
            "tnr": 120,
            "version_validator": 55,
            "postmortem_generator": 430,
        },
        "diagnosis": {
            "root_cause": (
                "notification-worker silently dropping emails due to character encoding "
                "mismatch. Worker deserializes SQS messages as UTF-8 but producer (user-svc) "
                "sends Latin-1 encoded payloads for Brazilian Portuguese names with accents "
                "(e.g., 'Jose' with accent). UnicodeDecodeError caught and swallowed by "
                "generic except block in process_message(). No error metrics incremented — "
                "only visible in DEBUG logs. ~23% of notifications silently dropped."
            ),
            "evidence": [
                "[LOG][notification-worker] DEBUG 'Skipping message msg-48291: UnicodeDecodeError: utf-8 codec can't decode byte 0xe9 in position 42' x847 (00:00→23:59) relevance=6.0",
                "[LOG][notification-worker] INFO 'Messages processed: 3,670 | Skipped: 847 | Success rate: 81.2%' (daily summary) relevance=5.5",
                "[LOG][user-svc] INFO 'Notification enqueued for user Jose (jose@email.com)' — note: name contains Latin-1 accent byte 0xe9 relevance=4.0",
                "[METRIC] notification-worker/messages_processed: stable at ~3,670/day (no anomaly detected — metric counts processed, not attempted)",
                "[METRIC] notification-worker/error_rate: 0% (errors caught silently, not counted in error metric)",
                "[TRACE] No trace anomalies — message processing completes in <50ms even when skipped (catch block returns immediately)",
                "[CORRELATION] No metric/trace signal — diagnosis relied entirely on log pattern analysis (Drain3 template 'Skipping message: UnicodeDecodeError' with count=847)",
            ],
            "confidence": 0.72,
            "proposed_fix": (
                "kubectl set env deployment/notification-worker SQS_ENCODING=latin-1 -n production"
            ),
            "risk_level": "LOW",
            "blast_radius": "notification-worker (email delivery for users with accented names, ~23% of total)",
            "total_duration_ms": 4850,
        },
    },

    # -------------------------------------------------------------------------
    # 5. Trace-only diagnosis — metricas ok, logs ok, trace mostra o problema
    # -------------------------------------------------------------------------
    {
        "title": "gRPC Deadline Exceeded: recommendation-engine",
        "severity": "critical",
        "service": "recommendation-engine",
        "node_durations": {
            "telemetry_sanitizer": 33,
            "log_parser": 290,
            "metric_filter": 180,
            "trace_filter": 350,
            "context_compressor": 170,
            "rag_retriever": 510,
            "root_cause_analyzer": 1850,
            "pre_execution_validator": 75,
            "tnr": 108,
            "version_validator": 48,
            "postmortem_generator": 370,
        },
        "diagnosis": {
            "root_cause": (
                "recommendation-engine calling ML model-serving via gRPC with 5s deadline. "
                "model-serving response time increased from 800ms to 6.2s after model update "
                "(v2 → v3, model size 2.4GB → 8.1GB). Every gRPC call exceeds deadline → "
                "DEADLINE_EXCEEDED error. Error only visible in traces (gRPC status code 4). "
                "Logs show generic 'recommendation unavailable' but don't expose the timeout. "
                "Metrics show error_rate increase but not the gRPC-specific cause."
            ),
            "evidence": [
                "[TRACE] product-page → recommendation-engine → model-serving (gRPC): 5,001ms → DEADLINE_EXCEEDED (status=4) | rpc.method=Predict | rpc.deadline=5000ms",
                "[TRACE] model-serving internal span: model.load_time=3,200ms (v3 model: 8.1GB) + model.inference_time=3,000ms = 6,200ms total (exceeds 5s deadline)",
                "[TRACE] Baseline traces (pre-update): model-serving span 800ms (model v2: 2.4GB) — within 5s deadline",
                "[LOG][recommendation-engine] WARN 'Recommendation unavailable, returning empty list' x4,521 (09:00→09:30) relevance=5.5",
                "[LOG][model-serving] INFO 'Model v3 loaded successfully, size=8.1GB, load_time=3.2s' at 08:55 relevance=7.0",
                "[METRIC] recommendation-engine/error_rate: 2% → 99.8% at 09:00 | model-serving/latency_p95: 800ms → 6,200ms | model-serving/error_rate: 0% (model-serving itself succeeds — it's the deadline that fails)",
                "[CORRELATION] Model update at 08:55 (INFO log) → first DEADLINE_EXCEEDED trace at 09:00:03 (5 min = model warm-up + first production request)",
            ],
            "confidence": 0.93,
            "proposed_fix": (
                "kubectl set env deployment/recommendation-engine GRPC_DEADLINE_MS=10000 -n production && "
                "kubectl set resources deployment/model-serving --requests=memory=16Gi --limits=memory=20Gi"
            ),
            "risk_level": "MEDIUM",
            "blast_radius": "recommendation-engine (product page recommendations empty, no purchase impact but UX degraded)",
            "total_duration_ms": 3985,
        },
    },

    # -------------------------------------------------------------------------
    # 6. Multi-signal correlation — 403 Forbidden + trace + metric combinados
    # -------------------------------------------------------------------------
    {
        "title": "HTTP 403 Forbidden: RBAC Policy Regression after Deploy",
        "severity": "critical",
        "service": "admin-api",
        "node_durations": {
            "telemetry_sanitizer": 36,
            "log_parser": 440,
            "metric_filter": 155,
            "trace_filter": 175,
            "context_compressor": 200,
            "rag_retriever": 470,
            "root_cause_analyzer": 1680,
            "pre_execution_validator": 82,
            "tnr": 112,
            "version_validator": 57,
            "postmortem_generator": 350,
        },
        "diagnosis": {
            "root_cause": (
                "RBAC policy ConfigMap updated in deploy v5.1.0 with incorrect role mapping. "
                "Role 'admin' mapped to permission set 'viewer' instead of 'full-access'. "
                "All admin users receiving HTTP 403 on write operations (POST/PUT/DELETE). "
                "Read operations (GET) still work — masking the issue for monitoring dashboards "
                "that only check availability (GET /health). Issue only caught by Drain3 "
                "correlating 403 logs with deploy timestamp."
            ),
            "evidence": [
                "[LOG][admin-api] ERROR 'HTTP 403 Forbidden: user role=admin lacks permission write:users on POST /api/users' x612 (11:00→11:30) relevance=11.5",
                "[LOG][admin-api] ERROR 'RBAC check failed: role=admin resolved to permission_set=viewer (expected: full-access)' x612 (11:00→11:30) relevance=12.0",
                "[LOG][admin-api] INFO 'ConfigMap rbac-policy-v5.1.0 loaded at startup' at 10:58 relevance=8.5",
                "[TRACE] admin-dashboard → admin-api (POST /api/users): 15ms → 403 Forbidden | rbac.role=admin | rbac.permission_set=viewer | rbac.required=write:users",
                "[TRACE] admin-dashboard → admin-api (GET /api/users): 12ms → 200 OK | rbac.role=admin — reads still work, writes blocked",
                "[METRIC] admin-api/http_403_count: 0 → 612 in 30min | admin-api/http_200_count: stable (GET requests unaffected) | admin-api/error_rate: 2% → 48% (only write path)",
                "[CORRELATION] Deploy v5.1.0 at 10:58 → ConfigMap load at 10:58 → first 403 at 11:00:03 (2 min = pod rollout + first admin write request)",
            ],
            "confidence": 0.95,
            "proposed_fix": (
                "kubectl edit configmap rbac-policy -n production  # fix role mapping: admin → full-access\n"
                "kubectl rollout restart deployment/admin-api -n production"
            ),
            "risk_level": "LOW",
            "blast_radius": "admin-api write operations only (admin users, ~15 people). Read operations and public API unaffected.",
            "total_duration_ms": 3757,
        },
    },

    # -------------------------------------------------------------------------
    # 7. HTTP 503 + Circuit Breaker — trace mostra circuit breaker abrindo
    # -------------------------------------------------------------------------
    {
        "title": "Circuit Breaker OPEN: payment-gateway → stripe-proxy",
        "severity": "critical",
        "service": "payment-gateway",
        "node_durations": {
            "telemetry_sanitizer": 40,
            "log_parser": 380,
            "metric_filter": 210,
            "trace_filter": 280,
            "context_compressor": 185,
            "rag_retriever": 530,
            "root_cause_analyzer": 1920,
            "pre_execution_validator": 88,
            "tnr": 125,
            "version_validator": 63,
            "postmortem_generator": 390,
        },
        "diagnosis": {
            "root_cause": (
                "stripe-proxy TLS certificate to Stripe API expired, causing all outbound "
                "HTTPS calls to fail with SSL_ERROR_EXPIRED_CERT. payment-gateway circuit "
                "breaker opened after 10 consecutive failures (threshold: 5). All payment "
                "attempts return HTTP 503 Service Unavailable. Stripe-side is healthy — "
                "problem is exclusively in our client certificate used for mTLS."
            ),
            "evidence": [
                "[LOG][stripe-proxy] ERROR 'SSL_ERROR_EXPIRED_CERT: certificate for stripe-proxy-mtls.pem expired at 2026-03-11T23:59:59Z' x3,240 (00:00→00:45) relevance=12.8",
                "[LOG][payment-gateway] WARN 'Circuit breaker OPEN for stripe-proxy: 10/10 failures in 30s window' at 00:00:32 relevance=10.5",
                "[LOG][payment-gateway] ERROR 'HTTP 503 Service Unavailable on POST /charge — circuit breaker is OPEN, rejecting request' x8,420 (00:00:32→00:45:00) relevance=9.8",
                "[LOG][stripe-proxy] INFO 'Last successful Stripe API call at 2026-03-11T23:59:58Z' — 1 second before cert expiry relevance=7.0",
                "[TRACE] checkout → payment-gateway → stripe-proxy: 2ms → 503 | circuit_breaker.state=OPEN | circuit_breaker.failures=10 | circuit_breaker.last_failure='SSL_ERROR_EXPIRED_CERT'",
                "[TRACE] payment-gateway internal: circuit breaker check (0.5ms) → reject (no outbound call made) — fast-fail pattern working correctly",
                "[METRIC] payment-gateway/http_503_count: 0 → 8,420 in 45min | stripe-proxy/ssl_errors: 0 → 3,240 | payment-gateway/circuit_breaker_state: CLOSED → OPEN at 00:00:32",
                "[CORRELATION] Cert expiry at 00:00:00 → first SSL error at 00:00:01 → circuit breaker OPEN at 00:00:32 (30s failure window) → 503s begin at 00:00:32",
            ],
            "confidence": 0.98,
            "proposed_fix": (
                "kubectl create secret tls stripe-proxy-mtls --cert=new-cert.pem --key=new-key.pem -n production --dry-run=client -o yaml | kubectl apply -f - && "
                "kubectl rollout restart deployment/stripe-proxy -n production"
            ),
            "risk_level": "LOW",
            "blast_radius": "All payment processing (payment-gateway + stripe-proxy). Browse/cart/auth unaffected.",
            "total_duration_ms": 4211,
        },
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
    cid = f"inc-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    delay = 0.3 if fast else 0.8

    print(f"\n{'='*70}")
    print(f"  Cenario {index}: {scenario['title']}")
    print(f"  Severity: {scenario['severity']} | Service: {scenario['service']}")
    print(f"  ID: {cid}")
    print(f"{'='*70}")

    # Pipeline started
    await notifier.on_event(evt(
        "pipeline.started", cid,
        title=scenario["title"],
        severity=scenario["severity"],
        service=scenario["service"],
    ))
    await asyncio.sleep(0.5 if fast else 1.5)

    # Nodes
    for node_name, duration_ms in scenario["node_durations"].items():
        print(f"  [{node_name}] {duration_ms}ms")
        await notifier.on_event(evt(
            "node.completed", cid,
            node_name=node_name,
            duration_ms=duration_ms,
        ))
        await asyncio.sleep(delay)

    # Pipeline completed
    await notifier.on_event(LifecycleEvent(
        event_type="pipeline.completed",
        correlation_id=cid,
        node_name=None,
        timestamp=datetime.now(timezone.utc),
        data=scenario["diagnosis"],
    ))
    print(f"  [DONE] Diagnostico enviado")
    return cid


async def main():
    parser = argparse.ArgumentParser(description="Cenarios de logs/traces para Slack")
    parser.add_argument("--scenario", type=int, help="Numero do cenario (1-7)")
    parser.add_argument("--fast", action="store_true", help="Delays reduzidos")
    args = parser.parse_args()

    channel = os.environ.get("SLACK_CHANNEL", "#aiops-alerts")
    creds = EnvCredentialProvider()
    notifier = SlackNotifier(channel=channel, creds=creds)

    if notifier._client is None:
        print("ERRO: SLACK_BOT_TOKEN nao configurado. Edite .env.local")
        return

    print(f"Canal: {channel}")
    print(f"Cenarios de Logs/Traces disponiveis: {len(SCENARIOS)}")
    print()
    for i, s in enumerate(SCENARIOS, 1):
        print(f"  {i}. {s['title']}")
    print()

    if args.scenario:
        if args.scenario < 1 or args.scenario > len(SCENARIOS):
            print(f"ERRO: cenario deve ser entre 1 e {len(SCENARIOS)}")
            return
        to_run = [(args.scenario, SCENARIOS[args.scenario - 1])]
    else:
        to_run = list(enumerate(SCENARIOS, 1))

    results = []
    for idx, scenario in to_run:
        cid = await run_scenario(notifier, scenario, idx, fast=args.fast)
        results.append((idx, scenario["title"], cid))
        if idx < len(to_run):
            await asyncio.sleep(1 if args.fast else 3)

    print(f"\n{'='*70}")
    print(f"  {len(results)} cenarios enviados para {channel}")
    print(f"{'='*70}")
    for idx, title, cid in results:
        print(f"  {idx}. {title}")
        print(f"     {cid}")


if __name__ == "__main__":
    asyncio.run(main())
