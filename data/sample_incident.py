"""
Gerador de incidente simulado realista.

Cenário: "Payment service timeout causado por connection pool exhaustion
após deploy de config errada que reduziu max_connections de 200 para 50."

Baseado nos padrões do paper OpsAgent (Nankai University, Oct 2025).
"""
from __future__ import annotations

import random
import datetime

# Re-exporta dataclasses para manter compatibilidade com imports existentes.
# Todos os consumers fazem: from data.sample_incident import LogEntry, ...
from data.models import LogEntry, MetricPoint, Span, SimulatedIncident  # noqa: F401
from data.registry import register_scenario, get_scenario

random.seed(42)

import data.memory_leak_scenario  # noqa: F401 — Registra cenário memory-leak no registry.
import data.unknown_fault_scenario  # noqa: F401 — Registra cenário unknown-fault no registry.

INCIDENT_START = datetime.datetime(2026, 3, 4, 14, 19, 0)
DEPLOY_TIME = datetime.datetime(2026, 3, 4, 14, 19, 0)
SPIKE_START = datetime.datetime(2026, 3, 4, 14, 23, 0)
DETECTION_TIME = datetime.datetime(2026, 3, 4, 14, 25, 0)
FIX_TIME = datetime.datetime(2026, 3, 4, 14, 28, 0)

SERVICES = ["payment-svc", "gateway", "auth-svc", "order-svc"]
DB_HOSTS = ["10.0.1.45", "10.0.1.46", "10.0.1.47"]


def generate_logs(count: int = 500) -> list[LogEntry]:
    """Gera logs simulados cobrindo 1 hora, com anomalia injetada."""
    logs = []
    base_time = INCIDENT_START - datetime.timedelta(minutes=30)

    # --- Logs normais PRE-incidente (14:00 - 14:19) ---
    for i in range(150):
        ts = base_time + datetime.timedelta(seconds=random.randint(0, 19 * 60))
        svc = random.choice(SERVICES)
        normal_msgs = [
            f"Request processed successfully in {random.randint(50, 150)}ms",
            f"Health check passed for {svc}",
            f"Connection acquired from pool ({random.randint(10, 40)}/200 active)",
            f"Cache hit for user session {random.randint(1000, 9999)}",
            f"Outbound request to {random.choice(SERVICES)} completed in {random.randint(20, 80)}ms",
        ]
        logs.append(LogEntry(ts, "INFO", svc, random.choice(normal_msgs)))

    # --- Deploy event (14:19) ---
    logs.append(LogEntry(
        DEPLOY_TIME,
        "INFO", "deployment-controller",
        "Deployment payment-svc updated: image=payment:v2.3.1, config=prod-2026-03-04"
    ))
    logs.append(LogEntry(
        DEPLOY_TIME + datetime.timedelta(seconds=5),
        "INFO", "payment-svc",
        "Configuration reloaded: max_connections=50, timeout=30s, pool_min_idle=5"
    ))
    logs.append(LogEntry(
        DEPLOY_TIME + datetime.timedelta(seconds=8),
        "WARN", "payment-svc",
        "Connection pool resized from 200 to 50 active connections"
    ))

    # --- Gradual degradation (14:19 - 14:23) ---
    for i in range(50):
        ts = DEPLOY_TIME + datetime.timedelta(seconds=random.randint(10, 4 * 60))
        warns = [
            f"Connection pool utilization high: {random.randint(40, 49)}/50 active",
            f"Slow query detected: {random.randint(500, 2000)}ms on SELECT from payments",
            f"Connection wait time exceeded {random.randint(100, 500)}ms",
        ]
        logs.append(LogEntry(ts, "WARN", "payment-svc", random.choice(warns)))

    # --- Full incident (14:23 - 14:28) ---
    for i in range(200):
        ts = SPIKE_START + datetime.timedelta(seconds=random.randint(0, 5 * 60))
        host = random.choice(DB_HOSTS)
        port = 5432
        error_msgs = [
            f"Connection to {host}:{port} timed out after 30s",
            f"Connection pool exhausted, max=50, waiting={random.randint(10, 100)}",
            f"Failed to acquire connection: pool full ({50}/50)",
            f"Transaction aborted: connection timeout after {random.randint(25, 35)}s",
            f"Circuit breaker OPEN for database calls ({random.randint(50, 200)} failures in 60s)",
        ]
        logs.append(LogEntry(ts, "ERROR", "payment-svc", random.choice(error_msgs)))

    # --- Cascade effects on other services ---
    for i in range(40):
        ts = SPIKE_START + datetime.timedelta(seconds=random.randint(30, 5 * 60))
        cascade_msgs = [
            ("gateway", f"Upstream payment-svc returned 503 after {random.randint(30000, 35000)}ms"),
            ("gateway", f"Circuit breaker tripped for payment-svc, routing to fallback"),
            ("order-svc", f"Payment confirmation timeout for order {random.randint(10000, 99999)}"),
            ("auth-svc", f"Token validation failed for request from payment-svc: connection refused"),
        ]
        svc, msg = random.choice(cascade_msgs)
        logs.append(LogEntry(ts, "ERROR", svc, msg))

    # --- Normal logs from healthy services ---
    for i in range(60):
        ts = SPIKE_START + datetime.timedelta(seconds=random.randint(0, 5 * 60))
        svc = random.choice(["auth-svc", "order-svc"])
        normal = [
            f"Request processed successfully in {random.randint(50, 150)}ms",
            f"Health check passed for {svc}",
        ]
        logs.append(LogEntry(ts, "INFO", svc, random.choice(normal)))

    logs.sort(key=lambda x: x.timestamp)
    return logs


def generate_metrics(interval_seconds: int = 15, duration_minutes: int = 60) -> list[MetricPoint]:
    """Gera séries temporais com anomalia injetada no payment-svc."""
    metrics = []
    base_time = INCIDENT_START - datetime.timedelta(minutes=30)
    num_points = (duration_minutes * 60) // interval_seconds

    for i in range(num_points):
        ts = base_time + datetime.timedelta(seconds=i * interval_seconds)

        is_incident = ts >= SPIKE_START
        is_degrading = DEPLOY_TIME <= ts < SPIKE_START

        # --- payment-svc metrics ---
        if is_incident:
            cpu = 85 + random.gauss(0, 3)
            memory = 78 + random.gauss(0, 2)
            latency = 4500 + random.gauss(0, 500)
            error_rate = 45 + random.gauss(0, 5)
            conn_pool = 50  # maxed out
        elif is_degrading:
            progress = (ts - DEPLOY_TIME).total_seconds() / (SPIKE_START - DEPLOY_TIME).total_seconds()
            cpu = 35 + progress * 50 + random.gauss(0, 2)
            memory = 55 + progress * 23 + random.gauss(0, 2)
            latency = 120 + progress * 4380 + random.gauss(0, 50)
            error_rate = 0.1 + progress * 44.9 + random.gauss(0, 1)
            conn_pool = 30 + progress * 20
        else:
            cpu = 35 + random.gauss(0, 3)
            memory = 55 + random.gauss(0, 2)
            latency = 120 + random.gauss(0, 15)
            error_rate = 0.1 + random.gauss(0, 0.05)
            conn_pool = random.randint(10, 40)

        metrics.append(MetricPoint(ts, "payment-svc", "cpu_usage_percent", max(0, cpu)))
        metrics.append(MetricPoint(ts, "payment-svc", "memory_usage_percent", max(0, memory)))
        metrics.append(MetricPoint(ts, "payment-svc", "latency_p99_ms", max(1, latency)))
        metrics.append(MetricPoint(ts, "payment-svc", "error_rate_percent", max(0, error_rate)))
        metrics.append(MetricPoint(ts, "payment-svc", "connection_pool_active", max(0, min(50, conn_pool))))

        # --- gateway metrics (healthy, slight impact) ---
        gw_latency = (4600 + random.gauss(0, 300)) if is_incident else (80 + random.gauss(0, 10))
        gw_error = (35 + random.gauss(0, 5)) if is_incident else (0.05 + random.gauss(0, 0.02))
        metrics.append(MetricPoint(ts, "gateway", "latency_p99_ms", max(1, gw_latency)))
        metrics.append(MetricPoint(ts, "gateway", "error_rate_percent", max(0, gw_error)))

        # --- auth-svc metrics (healthy) ---
        metrics.append(MetricPoint(ts, "auth-svc", "cpu_usage_percent", 20 + random.gauss(0, 3)))
        metrics.append(MetricPoint(ts, "auth-svc", "latency_p99_ms", 45 + random.gauss(0, 5)))

    return metrics


def generate_traces(count: int = 100) -> list[Span]:
    """Gera traces distribuídos com spans lentos no payment-svc."""
    traces = []

    for i in range(count):
        trace_id = f"trace-{i:04d}"
        ts_offset = random.randint(0, 60 * 60)
        base_ts = (INCIDENT_START - datetime.timedelta(minutes=30)) + datetime.timedelta(seconds=ts_offset)
        is_incident = base_ts >= SPIKE_START

        # Span 1: gateway (root)
        gw_duration = (5000 + random.gauss(0, 500)) if is_incident else (100 + random.gauss(0, 15))
        gw_span = Span(
            trace_id=trace_id,
            span_id=f"span-{i:04d}-gw",
            parent_span_id=None,
            service="gateway",
            operation="POST /api/payments",
            start_time=base_ts,
            duration_ms=max(1, gw_duration),
            status="ERROR" if is_incident else "OK",
        )
        traces.append(gw_span)

        # Span 2: payment-svc
        pay_duration = (4500 + random.gauss(0, 400)) if is_incident else (80 + random.gauss(0, 10))
        pay_span = Span(
            trace_id=trace_id,
            span_id=f"span-{i:04d}-pay",
            parent_span_id=gw_span.span_id,
            service="payment-svc",
            operation="process_payment",
            start_time=base_ts + datetime.timedelta(milliseconds=5),
            duration_ms=max(1, pay_duration),
            status="ERROR" if is_incident else "OK",
            attributes={
                "db.connection_wait_ms": (30000 + random.gauss(0, 2000)) if is_incident else (5 + random.gauss(0, 2)),
                "db.pool_active": 50 if is_incident else random.randint(10, 40),
                "db.pool_max": 50,
            },
        )
        traces.append(pay_span)

        # Span 3: postgres query
        if not is_incident or random.random() > 0.7:
            db_duration = (500 + random.gauss(0, 100)) if is_incident else (15 + random.gauss(0, 3))
            db_span = Span(
                trace_id=trace_id,
                span_id=f"span-{i:04d}-db",
                parent_span_id=pay_span.span_id,
                service="postgres",
                operation="SELECT payments WHERE id = ?",
                start_time=pay_span.start_time + datetime.timedelta(milliseconds=pay_duration - 20),
                duration_ms=max(1, db_duration),
                status="OK",
                attributes={"db.host": random.choice(DB_HOSTS)},
            )
            traces.append(db_span)

    return traces


def generate_payment_pool_exhaustion(seed: int = 42) -> SimulatedIncident:
    """Gera cenário de connection pool exhaustion no payment-svc.

    Cenário: deploy de config errada reduziu max_connections de 200 para 50,
    causando timeouts e circuit breaker sob carga normal.

    Args:
        seed: Semente para reprodutibilidade. Mesmo seed → mesma saída.

    Returns:
        SimulatedIncident com logs, métricas e traces do incidente.
    """
    random.seed(seed)
    return SimulatedIncident(
        logs=generate_logs(),
        metrics=generate_metrics(),
        traces=generate_traces(),
    )


# Registra o cenário no registry global.
register_scenario("payment-pool-exhaustion", generate_payment_pool_exhaustion)


def create_incident() -> SimulatedIncident:
    """Cria o incidente simulado completo.

    Mantida para backward-compatibility — delega ao registry.
    """
    return get_scenario("payment-pool-exhaustion")(seed=42)


if __name__ == "__main__":
    incident = create_incident()
    print(f"Logs gerados: {len(incident.logs)}")
    print(f"Metric points: {len(incident.metrics)}")
    print(f"Trace spans: {len(incident.traces)}")
    print(f"\nPrimeiros 5 logs:")
    for log in incident.logs[:5]:
        print(f"  {log}")
    print(f"\nRoot cause real: {incident.actual_root_cause}")
