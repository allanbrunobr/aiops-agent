"""
Gerador de cenário de incidente simulado: Memory Leak.

Cenário: "Memory leak no api-server v3.1.0 causado por conexões HTTP
não fechadas acumulando no heap, levando a OOM kills sob carga normal."

Segue o mesmo padrão de `sample_incident.py`, mas com timestamps,
serviços e padrões de telemetria distintos.

Módulo stdlib-only — não importa dependências pesadas (torch, faiss, etc.).
"""
from __future__ import annotations

import datetime
import random

from data.models import LogEntry, MetricPoint, SimulatedIncident, Span
from data.registry import register_scenario

# --- Constantes do cenário (timestamps distintos do payment-pool) ---
DEPLOY_TIME = datetime.datetime(2026, 3, 4, 16, 0, 0)
SPIKE_START = datetime.datetime(2026, 3, 4, 16, 15, 0)
DETECTION_TIME = datetime.datetime(2026, 3, 4, 16, 20, 0)
FIX_TIME = datetime.datetime(2026, 3, 4, 16, 30, 0)

# Serviços envolvidos — distintos do cenário payment-pool.
SERVICES = ["api-server", "worker-svc", "cache-svc", "db-proxy"]
PRIMARY_SERVICE = "api-server"

# Limite de heap do api-server (MB).
HEAP_LIMIT_MB = 2048


def generate_logs(count: int = 500) -> list[LogEntry]:
    """Gera logs simulados de um incidente de memory leak.

    Padrão temporal:
    - ~150 logs normais pré-incidente
    - ~3 logs de deploy
    - ~50 warnings de degradação gradual (memória subindo)
    - ~200 erros de incidente (OOM, heap exhaustion)
    - ~40 efeitos cascata em serviços dependentes
    - ~60 logs normais de serviços saudáveis
    """
    logs: list[LogEntry] = []
    base_time = DEPLOY_TIME - datetime.timedelta(minutes=30)

    # --- Logs normais PRE-incidente (15:30 - 16:00) ---
    for i in range(150):
        ts = base_time + datetime.timedelta(seconds=random.randint(0, 30 * 60))
        svc = random.choice(SERVICES)
        normal_msgs = [
            f"Request processed successfully in {random.randint(30, 120)}ms",
            f"Health check passed for {svc}",
            f"GC pause completed in {random.randint(10, 30)}ms, heap at {random.randint(35, 45)}%",
            f"Cache hit ratio: {random.randint(85, 98)}% for {svc}",
            f"Worker task completed: job_{random.randint(1000, 9999)} in {random.randint(50, 200)}ms",
        ]
        logs.append(LogEntry(ts, "INFO", svc, random.choice(normal_msgs)))

    # --- Deploy event (16:00) ---
    logs.append(LogEntry(
        DEPLOY_TIME,
        "INFO", "deployment-controller",
        "Deployment api-server updated: image=api-server:v3.1.0, config=prod-2026-03-04-mem",
    ))
    logs.append(LogEntry(
        DEPLOY_TIME + datetime.timedelta(seconds=3),
        "INFO", PRIMARY_SERVICE,
        "Application started: api-server v3.1.0 with HTTP client pool enabled",
    ))
    logs.append(LogEntry(
        DEPLOY_TIME + datetime.timedelta(seconds=5),
        "INFO", PRIMARY_SERVICE,
        "Configuration loaded: http_client_pool_size=unlimited, connection_cleanup=disabled",
    ))

    # --- Degradação gradual (16:00 - 16:15): memória subindo ---
    for i in range(50):
        ts = DEPLOY_TIME + datetime.timedelta(seconds=random.randint(30, 15 * 60))
        # Progresso de 0.0 a 1.0 dentro da janela de degradação.
        progress = (ts - DEPLOY_TIME).total_seconds() / (SPIKE_START - DEPLOY_TIME).total_seconds()
        progress = min(1.0, max(0.0, progress))
        heap_pct = int(40 + progress * 40)
        gc_ms = int(20 + progress * 300)
        warns = [
            f"Memory usage rising: {heap_pct}% of heap ({int(HEAP_LIMIT_MB * heap_pct / 100)}MB / {HEAP_LIMIT_MB}MB)",
            f"GC pause duration increased: {gc_ms}ms (threshold: 200ms)",
            f"Long GC pause detected: {gc_ms}ms, heap after GC: {heap_pct - random.randint(2, 8)}%",
            f"HTTP client connections growing: {int(50 + progress * 450)} open connections",
        ]
        logs.append(LogEntry(ts, "WARN", PRIMARY_SERVICE, random.choice(warns)))

    # --- Incidente completo (16:15 - 16:30): OOM kills, heap exhaustion ---
    for i in range(200):
        ts = SPIKE_START + datetime.timedelta(seconds=random.randint(0, 15 * 60))
        heap_mb = random.randint(1900, 2048)
        error_msgs = [
            f"OOM killer terminated process api-server (pid={random.randint(1000, 9999)}), used {heap_mb}MB",
            f"Java heap space exceeded: allocated {heap_mb}MB of {HEAP_LIMIT_MB}MB limit",
            f"Memory allocation failed: cannot allocate {random.randint(64, 256)}MB, heap at {heap_mb}MB/{HEAP_LIMIT_MB}MB",
            f"OutOfMemoryError: heap space exhausted, triggering emergency GC",
            f"Process restarted after OOM kill, uptime was {random.randint(30, 900)}s",
            f"Memory leak detected: {random.randint(500, 2000)} unclosed HTTP connections consuming {random.randint(200, 800)}MB",
        ]
        logs.append(LogEntry(ts, "ERROR", PRIMARY_SERVICE, random.choice(error_msgs)))

    # --- Efeitos cascata em serviços dependentes ---
    for i in range(40):
        ts = SPIKE_START + datetime.timedelta(seconds=random.randint(60, 15 * 60))
        cascade_msgs = [
            ("worker-svc", f"Upstream api-server returned 503 after {random.randint(5000, 30000)}ms"),
            ("worker-svc", f"Task dispatch failed: api-server unavailable (OOM restart in progress)"),
            ("cache-svc", f"Connection to api-server refused: service restarting"),
            ("db-proxy", f"Connection pool idle: api-server not sending queries (process down)"),
        ]
        svc, msg = random.choice(cascade_msgs)
        logs.append(LogEntry(ts, "ERROR", svc, msg))

    # --- Logs normais de serviços saudáveis ---
    for i in range(60):
        ts = SPIKE_START + datetime.timedelta(seconds=random.randint(0, 15 * 60))
        svc = random.choice(["cache-svc", "db-proxy"])
        normal = [
            f"Request processed successfully in {random.randint(30, 100)}ms",
            f"Health check passed for {svc}",
        ]
        logs.append(LogEntry(ts, "INFO", svc, random.choice(normal)))

    logs.sort(key=lambda x: x.timestamp)
    return logs


def generate_metrics(interval_seconds: int = 15, duration_minutes: int = 60) -> list[MetricPoint]:
    """Gera séries temporais com anomalia de memory leak no api-server.

    Métricas primárias: memory_usage_percent, cpu_usage_percent, gc_pause_ms,
    heap_used_mb, latency_p99_ms, error_rate_percent.
    Métricas de serviços dependentes: latência e CPU normais com leve impacto.
    """
    metrics: list[MetricPoint] = []
    base_time = DEPLOY_TIME - datetime.timedelta(minutes=30)
    num_points = (duration_minutes * 60) // interval_seconds

    for i in range(num_points):
        ts = base_time + datetime.timedelta(seconds=i * interval_seconds)

        is_incident = ts >= SPIKE_START
        is_degrading = DEPLOY_TIME <= ts < SPIKE_START

        # --- api-server metrics (primary) ---
        if is_incident:
            memory = 92 + random.gauss(0, 2)
            cpu = 75 + random.gauss(0, 5)
            gc_pause = 600 + random.gauss(0, 100)
            heap_used = 1950 + random.gauss(0, 50)
            latency = 3500 + random.gauss(0, 500)
            error_rate = 35 + random.gauss(0, 5)
        elif is_degrading:
            progress = (ts - DEPLOY_TIME).total_seconds() / (SPIKE_START - DEPLOY_TIME).total_seconds()
            memory = 40 + progress * 52 + random.gauss(0, 1.5)
            cpu = 30 + progress * 45 + random.gauss(0, 3)
            gc_pause = 20 + progress * 580 + random.gauss(0, 20)
            heap_used = 512 + progress * 1438 + random.gauss(0, 30)
            latency = 100 + progress * 3400 + random.gauss(0, 40)
            error_rate = 0.1 + progress * 34.9 + random.gauss(0, 0.5)
        else:
            memory = 40 + random.gauss(0, 2)
            cpu = 30 + random.gauss(0, 3)
            gc_pause = 20 + random.gauss(0, 5)
            heap_used = 512 + random.gauss(0, 30)
            latency = 100 + random.gauss(0, 12)
            error_rate = 0.1 + random.gauss(0, 0.03)

        metrics.append(MetricPoint(ts, PRIMARY_SERVICE, "memory_usage_percent", max(0, memory)))
        metrics.append(MetricPoint(ts, PRIMARY_SERVICE, "cpu_usage_percent", max(0, cpu)))
        metrics.append(MetricPoint(ts, PRIMARY_SERVICE, "gc_pause_ms", max(0, gc_pause)))
        metrics.append(MetricPoint(ts, PRIMARY_SERVICE, "heap_used_mb", max(0, heap_used)))
        metrics.append(MetricPoint(ts, PRIMARY_SERVICE, "latency_p99_ms", max(1, latency)))
        metrics.append(MetricPoint(ts, PRIMARY_SERVICE, "error_rate_percent", max(0, error_rate)))

        # --- worker-svc metrics (leve impacto durante incidente) ---
        wk_latency = (800 + random.gauss(0, 100)) if is_incident else (60 + random.gauss(0, 8))
        wk_cpu = (40 + random.gauss(0, 5)) if is_incident else (25 + random.gauss(0, 3))
        metrics.append(MetricPoint(ts, "worker-svc", "latency_p99_ms", max(1, wk_latency)))
        metrics.append(MetricPoint(ts, "worker-svc", "cpu_usage_percent", max(0, wk_cpu)))

        # --- cache-svc metrics (saudável) ---
        metrics.append(MetricPoint(ts, "cache-svc", "cpu_usage_percent", max(0, 15 + random.gauss(0, 2))))
        metrics.append(MetricPoint(ts, "cache-svc", "latency_p99_ms", max(1, 30 + random.gauss(0, 4))))

        # --- db-proxy metrics (saudável, leve impacto durante incidente) ---
        dbp_cpu = (22 + random.gauss(0, 3)) if is_incident else (12 + random.gauss(0, 2))
        dbp_latency = (45 + random.gauss(0, 8)) if is_incident else (15 + random.gauss(0, 3))
        metrics.append(MetricPoint(ts, "db-proxy", "cpu_usage_percent", max(0, dbp_cpu)))
        metrics.append(MetricPoint(ts, "db-proxy", "latency_p99_ms", max(1, dbp_latency)))

    return metrics


def generate_traces(count: int = 100) -> list[Span]:
    """Gera traces distribuídos com spans lentos no api-server durante incidente.

    Padrão: api-server → worker-svc → redis (3-span traces).
    Durante incidente: spans do api-server com alta latência e atributos de heap/GC.
    """
    traces: list[Span] = []

    for i in range(count):
        trace_id = f"trace-ml-{i:04d}"
        ts_offset = random.randint(0, 60 * 60)
        base_ts = (DEPLOY_TIME - datetime.timedelta(minutes=30)) + datetime.timedelta(seconds=ts_offset)
        is_incident = base_ts >= SPIKE_START

        # Span 1: api-server (root)
        api_duration = (4000 + random.gauss(0, 600)) if is_incident else (90 + random.gauss(0, 12))
        api_span = Span(
            trace_id=trace_id,
            span_id=f"span-ml-{i:04d}-api",
            parent_span_id=None,
            service=PRIMARY_SERVICE,
            operation="GET /api/tasks",
            start_time=base_ts,
            duration_ms=max(1, api_duration),
            status="ERROR" if is_incident else "OK",
            attributes={
                "jvm.heap_used_mb": (1950 + random.gauss(0, 50)) if is_incident else (512 + random.gauss(0, 30)),
                "gc.pause_ms": (600 + random.gauss(0, 100)) if is_incident else (20 + random.gauss(0, 5)),
            },
        )
        traces.append(api_span)

        # Span 2: worker-svc
        wk_duration = (1200 + random.gauss(0, 200)) if is_incident else (50 + random.gauss(0, 8))
        wk_span = Span(
            trace_id=trace_id,
            span_id=f"span-ml-{i:04d}-wk",
            parent_span_id=api_span.span_id,
            service="worker-svc",
            operation="process_task",
            start_time=base_ts + datetime.timedelta(milliseconds=5),
            duration_ms=max(1, wk_duration),
            status="OK",
        )
        traces.append(wk_span)

        # Span 3: redis (não aparece se processo morreu antes)
        if not is_incident or random.random() > 0.6:
            redis_duration = (200 + random.gauss(0, 40)) if is_incident else (8 + random.gauss(0, 2))
            redis_span = Span(
                trace_id=trace_id,
                span_id=f"span-ml-{i:04d}-rd",
                parent_span_id=wk_span.span_id,
                service="redis",
                operation="GET cache:task:*",
                start_time=wk_span.start_time + datetime.timedelta(milliseconds=wk_duration - 10),
                duration_ms=max(1, redis_duration),
                status="OK",
                attributes={"db.system": "redis"},
            )
            traces.append(redis_span)

    return traces


def generate_memory_leak(seed: int = 42) -> SimulatedIncident:
    """Gera cenário de memory leak no api-server.

    Cenário: deploy da versão v3.1.0 introduziu conexões HTTP não fechadas
    que acumulam no heap, causando OOM kills sob carga normal.

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
        deploy_time=DEPLOY_TIME,
        spike_start=SPIKE_START,
        detection_time=DETECTION_TIME,
        fix_time=FIX_TIME,
        actual_root_cause=(
            "Memory leak in api-server v3.1.0 caused by unclosed HTTP client "
            "connections accumulating in heap, leading to OOM kills under normal load."
        ),
        actual_fix="Rollback api-server to v3.0.2 and apply connection pool cleanup patch",
    )


# Registra o cenário no registry global ao importar o módulo.
register_scenario("memory-leak", generate_memory_leak)


if __name__ == "__main__":
    incident = generate_memory_leak(seed=42)
    print(f"Logs gerados: {len(incident.logs)}")
    print(f"Metric points: {len(incident.metrics)}")
    print(f"Trace spans: {len(incident.traces)}")
    print(f"\nPrimeiros 5 logs:")
    for log in incident.logs[:5]:
        print(f"  {log}")
    print(f"\nRoot cause real: {incident.actual_root_cause}")
    print(f"Fix: {incident.actual_fix}")
