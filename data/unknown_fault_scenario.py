"""
Gerador de cenário de incidente simulado: Unknown Fault Type.

Cenário: "Falhas intermitentes no data-processor v4.0.0 com erros de
serialização e corrupção de estado interno, sem padrão reconhecível
pelos heurísticos do diagnoser (pool, memory, timeout)."

Nenhum log contém keywords de padrões conhecidos (connection pool,
memory/oom/heap, timeout, timed out). Isso força _heuristic_diagnosis()
a retornar confidence="low" e o mitigator a usar o padrão "unknown".

Módulo stdlib-only — não importa dependências pesadas (torch, faiss, etc.).
"""
from __future__ import annotations

import datetime
import random

from data.models import LogEntry, MetricPoint, SimulatedIncident, Span
from data.registry import register_scenario

# --- Constantes do cenário (timestamps distintos dos demais) ---
DEPLOY_TIME = datetime.datetime(2026, 3, 5, 10, 0, 0)
SPIKE_START = datetime.datetime(2026, 3, 5, 10, 12, 0)
DETECTION_TIME = datetime.datetime(2026, 3, 5, 10, 18, 0)
FIX_TIME = datetime.datetime(2026, 3, 5, 10, 35, 0)

# Serviços envolvidos — distintos dos demais cenários.
SERVICES = ["data-processor", "ingest-svc", "scheduler", "storage-gw"]
PRIMARY_SERVICE = "data-processor"


def generate_logs(count: int = 500) -> list[LogEntry]:
    """Gera logs simulados de um incidente com fault type desconhecido.

    IMPORTANTE: Nenhuma mensagem de erro contém keywords dos padrões
    heurísticos conhecidos (pool, memory, oom, heap, timeout, timed out,
    connection pool, pool exhausted). Isso garante que _heuristic_diagnosis()
    não consiga classificar o fault type.

    Padrão temporal:
    - ~150 logs normais pré-incidente
    - ~3 logs de deploy
    - ~50 warnings de degradação gradual
    - ~200 erros de incidente (serialização, corrupção de estado)
    - ~40 efeitos cascata em serviços dependentes
    - ~60 logs normais de serviços saudáveis
    """
    logs: list[LogEntry] = []
    base_time = DEPLOY_TIME - datetime.timedelta(minutes=30)

    # --- Logs normais PRE-incidente (09:30 - 10:00) ---
    for i in range(150):
        ts = base_time + datetime.timedelta(seconds=random.randint(0, 30 * 60))
        svc = random.choice(SERVICES)
        normal_msgs = [
            f"Request processed successfully in {random.randint(30, 120)}ms",
            f"Health check passed for {svc}",
            f"Batch processed: {random.randint(100, 500)} records in {random.randint(50, 200)}ms",
            f"Cache refresh completed for {svc}",
            f"Scheduler tick: {random.randint(5, 20)} pending jobs dispatched",
        ]
        logs.append(LogEntry(ts, "INFO", svc, random.choice(normal_msgs)))

    # --- Deploy event (10:00) ---
    logs.append(LogEntry(
        DEPLOY_TIME,
        "INFO", "deployment-controller",
        "Deployment data-processor updated: image=data-processor:v4.0.0, config=prod-2026-03-05-ser",
    ))
    logs.append(LogEntry(
        DEPLOY_TIME + datetime.timedelta(seconds=3),
        "INFO", PRIMARY_SERVICE,
        "Application started: data-processor v4.0.0 with new serialization codec",
    ))
    logs.append(LogEntry(
        DEPLOY_TIME + datetime.timedelta(seconds=5),
        "INFO", PRIMARY_SERVICE,
        "Configuration loaded: codec=avro-v2, batch_size=1000, compression=snappy",
    ))

    # --- Degradação gradual (10:00 - 10:12): erros intermitentes ---
    for i in range(50):
        ts = DEPLOY_TIME + datetime.timedelta(seconds=random.randint(30, 12 * 60))
        progress = (ts - DEPLOY_TIME).total_seconds() / (SPIKE_START - DEPLOY_TIME).total_seconds()
        progress = min(1.0, max(0.0, progress))
        err_rate = int(2 + progress * 15)
        warns = [
            f"Serialization error rate rising: {err_rate}% of payloads failing validation",
            f"Internal state checksum mismatch: expected {random.randint(1000, 9999):04x}, got {random.randint(1000, 9999):04x}",
            f"Retry count elevated: {int(3 + progress * 20)} retries in last 60s",
            f"Payload validation failure: field 'event_id' has unexpected null value",
        ]
        logs.append(LogEntry(ts, "WARN", PRIMARY_SERVICE, random.choice(warns)))

    # --- Incidente completo (10:12 - 10:35): erros de serialização e corrupção ---
    for i in range(200):
        ts = SPIKE_START + datetime.timedelta(seconds=random.randint(0, 23 * 60))
        error_msgs = [
            f"Unrecoverable serialization error: payload {random.randint(10000, 99999)} rejected by codec",
            f"Internal state corruption detected: partition {random.randint(0, 15)} inconsistent",
            f"Data integrity violation: CRC32 mismatch on batch {random.randint(100, 999)}",
            f"Processing pipeline halted: unhandled exception in transform stage {random.randint(1, 5)}",
            f"Fatal: record {random.randint(10000, 99999)} failed schema evolution check",
            f"Unexpected null reference in aggregation step for key '{random.choice(['user', 'event', 'session'])}_{random.randint(100, 999)}'",
        ]
        logs.append(LogEntry(ts, "ERROR", PRIMARY_SERVICE, random.choice(error_msgs)))

    # --- Efeitos cascata em serviços dependentes ---
    for i in range(40):
        ts = SPIKE_START + datetime.timedelta(seconds=random.randint(60, 23 * 60))
        cascade_msgs = [
            ("ingest-svc", f"Upstream data-processor returned 503 after {random.randint(5000, 15000)}ms"),
            ("ingest-svc", f"Batch ingestion stalled: data-processor not accepting records"),
            ("scheduler", f"Job dispatch failed: data-processor reporting unhealthy"),
            ("storage-gw", f"Write pipeline blocked: data-processor not flushing buffers"),
        ]
        svc, msg = random.choice(cascade_msgs)
        logs.append(LogEntry(ts, "ERROR", svc, msg))

    # --- Logs normais de serviços saudáveis ---
    for i in range(60):
        ts = SPIKE_START + datetime.timedelta(seconds=random.randint(0, 23 * 60))
        svc = random.choice(["scheduler", "storage-gw"])
        normal = [
            f"Request processed successfully in {random.randint(30, 100)}ms",
            f"Health check passed for {svc}",
        ]
        logs.append(LogEntry(ts, "INFO", svc, random.choice(normal)))

    logs.sort(key=lambda x: x.timestamp)
    return logs


def generate_metrics(interval_seconds: int = 15, duration_minutes: int = 60) -> list[MetricPoint]:
    """Gera séries temporais com anomalia no data-processor.

    Métricas primárias: error_rate_percent, cpu_usage_percent, latency_p99_ms,
    throughput_rps, retry_rate_percent.
    Métricas de serviços dependentes: latência e CPU normais com leve impacto.
    """
    metrics: list[MetricPoint] = []
    base_time = DEPLOY_TIME - datetime.timedelta(minutes=30)
    num_points = (duration_minutes * 60) // interval_seconds

    for i in range(num_points):
        ts = base_time + datetime.timedelta(seconds=i * interval_seconds)

        is_incident = ts >= SPIKE_START
        is_degrading = DEPLOY_TIME <= ts < SPIKE_START

        # --- data-processor metrics (primary) ---
        if is_incident:
            error_rate = 40 + random.gauss(0, 5)
            cpu = 60 + random.gauss(0, 5)
            latency = 2500 + random.gauss(0, 300)
            throughput = 50 + random.gauss(0, 10)
            retry_rate = 35 + random.gauss(0, 4)
        elif is_degrading:
            progress = (ts - DEPLOY_TIME).total_seconds() / (SPIKE_START - DEPLOY_TIME).total_seconds()
            error_rate = 0.5 + progress * 39.5 + random.gauss(0, 1)
            cpu = 35 + progress * 25 + random.gauss(0, 3)
            latency = 80 + progress * 2420 + random.gauss(0, 30)
            throughput = 500 - progress * 450 + random.gauss(0, 15)
            retry_rate = 1 + progress * 34 + random.gauss(0, 0.5)
        else:
            error_rate = 0.5 + random.gauss(0, 0.1)
            cpu = 35 + random.gauss(0, 3)
            latency = 80 + random.gauss(0, 10)
            throughput = 500 + random.gauss(0, 20)
            retry_rate = 1 + random.gauss(0, 0.2)

        metrics.append(MetricPoint(ts, PRIMARY_SERVICE, "error_rate_percent", max(0, error_rate)))
        metrics.append(MetricPoint(ts, PRIMARY_SERVICE, "cpu_usage_percent", max(0, cpu)))
        metrics.append(MetricPoint(ts, PRIMARY_SERVICE, "latency_p99_ms", max(1, latency)))
        metrics.append(MetricPoint(ts, PRIMARY_SERVICE, "throughput_rps", max(0, throughput)))
        metrics.append(MetricPoint(ts, PRIMARY_SERVICE, "retry_rate_percent", max(0, retry_rate)))

        # --- ingest-svc metrics (leve impacto durante incidente) ---
        ing_latency = (600 + random.gauss(0, 80)) if is_incident else (40 + random.gauss(0, 5))
        ing_cpu = (30 + random.gauss(0, 4)) if is_incident else (20 + random.gauss(0, 2))
        metrics.append(MetricPoint(ts, "ingest-svc", "latency_p99_ms", max(1, ing_latency)))
        metrics.append(MetricPoint(ts, "ingest-svc", "cpu_usage_percent", max(0, ing_cpu)))

        # --- scheduler metrics (saudável) ---
        metrics.append(MetricPoint(ts, "scheduler", "cpu_usage_percent", max(0, 10 + random.gauss(0, 2))))
        metrics.append(MetricPoint(ts, "scheduler", "latency_p99_ms", max(1, 20 + random.gauss(0, 3))))

        # --- storage-gw metrics (saudável, leve impacto durante incidente) ---
        stg_cpu = (18 + random.gauss(0, 3)) if is_incident else (10 + random.gauss(0, 2))
        stg_latency = (35 + random.gauss(0, 6)) if is_incident else (12 + random.gauss(0, 2))
        metrics.append(MetricPoint(ts, "storage-gw", "cpu_usage_percent", max(0, stg_cpu)))
        metrics.append(MetricPoint(ts, "storage-gw", "latency_p99_ms", max(1, stg_latency)))

    return metrics


def generate_traces(count: int = 100) -> list[Span]:
    """Gera traces distribuídos com spans lentos no data-processor durante incidente.

    Padrão: data-processor → ingest-svc → storage-gw (3-span traces).
    Durante incidente: spans do data-processor com alta latência e erros.
    """
    traces: list[Span] = []

    for i in range(count):
        trace_id = f"trace-uf-{i:04d}"
        ts_offset = random.randint(0, 60 * 60)
        base_ts = (DEPLOY_TIME - datetime.timedelta(minutes=30)) + datetime.timedelta(seconds=ts_offset)
        is_incident = base_ts >= SPIKE_START

        # Span 1: data-processor (root)
        dp_duration = (3000 + random.gauss(0, 400)) if is_incident else (70 + random.gauss(0, 10))
        dp_span = Span(
            trace_id=trace_id,
            span_id=f"span-uf-{i:04d}-dp",
            parent_span_id=None,
            service=PRIMARY_SERVICE,
            operation="POST /api/process-batch",
            start_time=base_ts,
            duration_ms=max(1, dp_duration),
            status="ERROR" if is_incident else "OK",
            attributes={
                "batch.size": random.randint(100, 1000),
                "codec.errors": (random.randint(10, 50)) if is_incident else 0,
            },
        )
        traces.append(dp_span)

        # Span 2: ingest-svc
        ing_duration = (800 + random.gauss(0, 150)) if is_incident else (35 + random.gauss(0, 5))
        ing_span = Span(
            trace_id=trace_id,
            span_id=f"span-uf-{i:04d}-ing",
            parent_span_id=dp_span.span_id,
            service="ingest-svc",
            operation="ingest_records",
            start_time=base_ts + datetime.timedelta(milliseconds=5),
            duration_ms=max(1, ing_duration),
            status="OK",
        )
        traces.append(ing_span)

        # Span 3: storage-gw (não aparece se processo falhou antes)
        if not is_incident or random.random() > 0.5:
            stg_duration = (150 + random.gauss(0, 30)) if is_incident else (10 + random.gauss(0, 2))
            stg_span = Span(
                trace_id=trace_id,
                span_id=f"span-uf-{i:04d}-stg",
                parent_span_id=ing_span.span_id,
                service="storage-gw",
                operation="PUT blob:batch:*",
                start_time=ing_span.start_time + datetime.timedelta(milliseconds=ing_duration - 10),
                duration_ms=max(1, stg_duration),
                status="OK",
                attributes={"storage.backend": "s3"},
            )
            traces.append(stg_span)

    return traces


def generate_unknown_fault(seed: int = 42) -> SimulatedIncident:
    """Gera cenário de fault type desconhecido no data-processor.

    Cenário: deploy da versão v4.0.0 introduziu um novo codec de serialização
    que causa corrupção de dados intermitente sob carga normal. Os erros não
    correspondem a nenhum padrão heurístico conhecido (pool, memory, timeout).

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
            "Serialization codec corruption in data-processor v4.0.0 caused by "
            "incompatible Avro schema evolution under concurrent writes."
        ),
        actual_fix="Rollback data-processor to v3.9.2 and fix schema evolution logic",
    )


# Registra o cenário no registry global ao importar o módulo.
register_scenario("unknown-fault", generate_unknown_fault)


if __name__ == "__main__":
    incident = generate_unknown_fault(seed=42)
    print(f"Logs gerados: {len(incident.logs)}")
    print(f"Metric points: {len(incident.metrics)}")
    print(f"Trace spans: {len(incident.traces)}")
    print(f"\nPrimeiros 5 logs:")
    for log in incident.logs[:5]:
        print(f"  {log}")
    print(f"\nRoot cause real: {incident.actual_root_cause}")
    print(f"Fix: {incident.actual_fix}")
