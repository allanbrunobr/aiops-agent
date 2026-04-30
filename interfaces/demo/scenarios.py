from __future__ import annotations
"""
Corpora de cenários de demonstração: dados pré-gerados determinísticos
para replay no motor de simulação.

Cada corpus é uma lista de (sim_timestamp, item) onde item pode ser
LogEntry, MetricPoint ou Span. Todos usam seeds fixas para reprodutibilidade.
"""

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from data.models import LogEntry, MetricPoint, Span


# Data base de simulação (UTC)
_BASE_TIME = datetime(2026, 3, 20, 10, 0, 0, tzinfo=timezone.utc)

CorpusItem = tuple[datetime, LogEntry | MetricPoint | Span]


@dataclass
class DemoScenarioConfig:
    """Configuração do cenário de demonstração."""
    speed_factor: float = 4.0
    enable_prediction: bool = True
    enable_rag: bool = True
    enable_llm: bool = False
    risk_warning_threshold: float = 60.0
    risk_alert_threshold: float = 80.0
    ewma_threshold: float = 0.90  # limiar para ETA; override por cenário (ex: 7200.0 para MB)


def build_payment_pool_corpus() -> list[CorpusItem]:
    """Corpus realista para o cenário payment-pool-exhaustion.

    Simula 32 minutos de esgotamento progressivo e não-linear do pool de conexões.
    Usa curva logística para progressão realista (lenta no início, rápida no meio,
    estabiliza no topo). Log volumes escalam exponencialmente com a degradação,
    permitindo que o detector de burst identifique o incidente ~min 18-20.

    Fases:
      Normal      (0-7):   3-4 logs/min, latência 80-200ms, pool 5-25%
      Degradação  (8-15):  6-10 logs/min, latência 200-600ms, pool 25-50%
      Incidente   (16-23): 12-20 logs/min, latência 600-2000ms, pool 50-75%
      Exaustão    (24-31): 25-40 logs/min, latência 2000-8000ms, pool 75-86%

    Risk score esperado:
      ~min 10: score ≈ 40 (latency drift detectado, burst ainda não)
      ~min 18: score ≈ 65 (burst_detected=True, rate_ratio ~2.5)
      ~min 22: score ≈ 92 (burst rate_ratio ~3+, ALERT → INCIDENT)
    """
    random.seed(42)
    corpus: list[CorpusItem] = []
    duration_minutes = 32
    MAX_POOL = 200  # max_connections configurado no serviço
    BASE_LATENCY_MS = 80.0

    # Templates de log por fase — sem dados variáveis para que o detector de burst
    # foque no volume, não na novidade (novidade adiciona +20 ao score mas é bônus)
    _TEMPLATES_NORMAL = [
        "Conexão obtida do pool",
        "Transação processada com sucesso",
        "Cache hit confirmado",
        "Requisição completada",
        "Health check OK",
    ]
    _TEMPLATES_EARLY = [
        "Pool timeout — tentando reconectar",
        "WARN: connection_pool_active acima do limiar",
        "Retry aguardando conexão disponível",
        "Conexão demorou mais que o esperado",
    ]
    _TEMPLATES_ACTIVE = [
        "Pool timeout crítico — tentando reconectar",
        "WARN: connection_pool_active crítico",
        "Múltiplos retries — pool sob pressão",
        "Conexão recusada: pool_active alto",
        "Erro ao adquirir conexão: PoolExhaustedException",
        "Circuit breaker abrindo",
    ]
    _TEMPLATES_EXHAUSTION = [
        "Erro ao adquirir conexão: PoolExhaustedException",
        "CRITICAL: connection pool esgotado",
        "Retry esgotado — sem conexões disponíveis",
        "Transação abortada: timeout de pool global atingido",
        "Circuit breaker OPEN — serviço indisponível",
        "Cascade failure detectado",
        "Connection queue overflow — requests aguardando",
    ]

    services = ["payment-service", "db-pool", "api-gateway", "cache"]

    for minute in range(duration_minutes):
        ts = _BASE_TIME + timedelta(minutes=minute)
        progress = minute / (duration_minutes - 1)  # 0.0 → 1.0

        # === Métrica: pool_active com curva logística (aceleração realista) ===
        # Logística centrada em 45% do tempo: cresce devagar, acelera no meio, estabiliza
        logistic = 1.0 / (1.0 + math.exp(-8.0 * (progress - 0.45)))
        pool_frac = 0.05 + logistic * 0.82 + random.gauss(0, 0.005)
        pool_frac = max(0.0, min(1.0, pool_frac))
        pool_active_count = int(pool_frac * MAX_POOL)

        # 2 readings por minuto (mais granularidade para EWMA slope)
        for sub_sec in (0, 30):
            sub_ts = ts + timedelta(seconds=sub_sec)
            noise = random.gauss(0, 0.008)
            frac = max(0.0, min(1.0, pool_frac + noise))

            corpus.append((sub_ts, MetricPoint(
                timestamp=sub_ts, service="db-pool",
                metric_name="connection_pool_active", value=frac,
            )))

            cpu = 0.22 + progress * 0.38 + random.gauss(0, 0.02)
            corpus.append((sub_ts, MetricPoint(
                timestamp=sub_ts, service="payment-service",
                metric_name="cpu_usage", value=max(0.0, min(1.0, cpu)),
            )))

            mem = 0.38 + progress * 0.22 + random.gauss(0, 0.01)
            corpus.append((sub_ts, MetricPoint(
                timestamp=sub_ts, service="payment-service",
                metric_name="memory_usage", value=max(0.0, min(1.0, mem)),
            )))

            # Latência não-linear: quadrática quando pool > 50% (fila de espera explode)
            if pool_frac <= 0.5:
                lat_p95 = BASE_LATENCY_MS + pool_frac * 350.0
            else:
                # Aceleração quadrática após 50%: latência de fila de espera de conexão
                lat_p95 = BASE_LATENCY_MS + 175.0 + ((pool_frac - 0.5) ** 2) * 9000.0
            lat_p95 = max(50.0, lat_p95 + random.gauss(0, lat_p95 * 0.06))
            lat_p99 = max(60.0, lat_p95 * 1.45 + random.gauss(0, 25.0))

            corpus.append((sub_ts, MetricPoint(
                timestamp=sub_ts, service="api-gateway",
                metric_name="latency_p95_ms", value=lat_p95,
            )))
            corpus.append((sub_ts, MetricPoint(
                timestamp=sub_ts, service="api-gateway",
                metric_name="latency_p99_ms", value=lat_p99,
            )))

        # === Logs: volume escala com a fase de degradação ===
        if minute < 8:
            n_logs = random.randint(3, 4)
            phase = "normal"
        elif minute < 16:
            n_logs = random.randint(6, 10)
            phase = "early"
        elif minute < 24:
            n_logs = random.randint(12, 20)
            phase = "active"
        else:
            n_logs = random.randint(25, 40)
            phase = "exhaustion"

        for _ in range(n_logs):
            # Sub-minuto aleatório: essencial para time-based burst detection
            log_ts = ts + timedelta(seconds=random.randint(0, 59))
            svc = random.choice(services)

            if phase == "normal":
                level = "INFO"
                msg = random.choice(_TEMPLATES_NORMAL)

            elif phase == "early":
                if random.random() < 0.35:
                    level = "WARNING"
                    base = random.choice(_TEMPLATES_EARLY)
                    msg = f"{base} — pool={pool_active_count}/{MAX_POOL}"
                else:
                    level = "INFO"
                    msg = random.choice(_TEMPLATES_NORMAL)

            elif phase == "active":
                rand = random.random()
                if rand < 0.55:
                    level = "WARNING"
                    base = random.choice(_TEMPLATES_ACTIVE[:4])
                    msg = f"{base} — pool={pool_active_count}/{MAX_POOL}"
                elif rand < 0.80:
                    level = "ERROR"
                    msg = random.choice(_TEMPLATES_ACTIVE[4:])
                else:
                    level = "INFO"
                    msg = "Conexão obtida do pool — tentativa 2"

            else:  # exhaustion
                if random.random() < 0.78:
                    level = "ERROR"
                    msg = random.choice(_TEMPLATES_EXHAUSTION)
                else:
                    level = "WARNING"
                    msg = f"Pool timeout crítico — {pool_active_count}/{MAX_POOL} conexões ativas"

            corpus.append((log_ts, LogEntry(
                timestamp=log_ts, level=level, service=svc, message=msg,
            )))

        # === Spans: 2-4 por minuto com child spans (trace distribuído realista) ===
        n_spans = random.randint(2, 5 if minute >= 16 else 3)

        for span_i in range(n_spans):
            span_ts = ts + timedelta(seconds=random.randint(0, 50))

            # Root: api-gateway recebe a requisição
            if pool_frac <= 0.5:
                root_dur = BASE_LATENCY_MS + pool_frac * 400.0 + random.gauss(0, 15.0)
            else:
                root_dur = BASE_LATENCY_MS + 200.0 + ((pool_frac - 0.5) ** 2) * 9000.0 + random.gauss(0, 60.0)
            root_dur = max(10.0, root_dur)

            # Status muda para ERROR quando pool > 65% com probabilidade crescente
            error_prob = max(0.0, (pool_frac - 0.65) * 3.0)
            span_status = "ERROR" if random.random() < error_prob else "OK"

            trace_id = f"tr-{minute:04d}-{span_i}"
            root_id = f"sp-gw-{minute:04d}-{span_i}"

            corpus.append((span_ts, Span(
                trace_id=trace_id,
                span_id=root_id,
                parent_span_id=None,
                service="api-gateway",
                operation="POST /payment",
                start_time=span_ts,
                duration_ms=root_dur,
                status=span_status,
            )))

            # Child: payment-service aguardando conexão do pool
            acquire_dur = max(5.0, root_dur * 0.88 + random.gauss(0, 10.0))
            pay_id = f"sp-pay-{minute:04d}-{span_i}"
            corpus.append((span_ts, Span(
                trace_id=trace_id,
                span_id=pay_id,
                parent_span_id=root_id,
                service="payment-service",
                operation="db.pool.acquire",
                start_time=span_ts + timedelta(milliseconds=5),
                duration_ms=acquire_dur,
                status=span_status,
            )))

            # Grandchild: db-pool TCP connect — só aparece quando pool > 40%
            if pool_frac > 0.40:
                tcp_dur = max(5.0, acquire_dur * 0.75 + random.gauss(0, 12.0))
                db_status = "ERROR" if pool_frac > 0.70 and span_status == "ERROR" else "OK"
                corpus.append((span_ts, Span(
                    trace_id=trace_id,
                    span_id=f"sp-db-{minute:04d}-{span_i}",
                    parent_span_id=pay_id,
                    service="db-pool",
                    operation="tcp.connect",
                    start_time=span_ts + timedelta(milliseconds=12),
                    duration_ms=tcp_dur,
                    status=db_status,
                )))

    corpus.sort(key=lambda x: x[0])
    return corpus


def build_payment_pool_recovery_corpus() -> list[CorpusItem]:
    """Corpus para o cenário payment-pool-exhaustion com recuperação.

    Simula 40 minutos em 3 atos:
      Ato 1 — Degradação (0-20 min): pool sobe de 5% a 82% com curva logística.
        Log volumes crescem de 3/min → 30/min. Score sobe até ~85.
      Ato 2 — Incidente (21-24 min): pool estabiliza em ~82%, error storm máximo.
        Score sustentado acima de 80, pipeline AIOps dispara.
      Ato 3 — Recuperação (25-40 min): ops aumenta max_connections (200→500).
        Pool despressuriza rapidamente para ~15%. Logs voltam a INFO.
        Latência normaliza. RiskTracker decai → score volta a < 30.

    Demonstra para o time de SRE que o sistema detecta TANTO a piora quanto
    a recuperação — sem falsos positivos após o incidente ser resolvido.
    Usa random.seed(44) para reprodutibilidade.
    """
    import math as _math
    random.seed(44)
    corpus: list[CorpusItem] = []

    MAX_POOL_BEFORE = 200   # max_connections antes do fix
    MAX_POOL_AFTER  = 500   # max_connections após fix operacional
    BASE_LATENCY_MS = 80.0
    FIX_MINUTE      = 25    # minuto em que ops aplica o fix

    _TEMPLATES_NORMAL = [
        "Conexão obtida do pool",
        "Transação processada com sucesso",
        "Cache hit confirmado",
        "Requisição completada",
        "Health check OK",
    ]
    _TEMPLATES_DEGRADED = [
        "Pool timeout crítico — tentando reconectar",
        "WARN: connection_pool_active crítico",
        "Múltiplos retries — pool sob pressão",
        "Conexão recusada: pool_active alto",
        "Erro ao adquirir conexão: PoolExhaustedException",
        "Circuit breaker abrindo",
    ]
    _TEMPLATES_INCIDENT = [
        "Erro ao adquirir conexão: PoolExhaustedException",
        "CRITICAL: connection pool esgotado",
        "Retry esgotado — sem conexões disponíveis",
        "Transação abortada: timeout de pool global atingido",
        "Circuit breaker OPEN — serviço indisponível",
        "Cascade failure detectado",
    ]
    _TEMPLATES_RECOVERY = [
        "Conexão obtida do pool",
        "Transação processada com sucesso",
        "Health check OK",
        "Cache hit confirmado",
        "Requisição completada",
        "Conexão disponível — sem timeout",
    ]

    services = ["payment-service", "db-pool", "api-gateway", "cache"]

    for minute in range(40):
        ts = _BASE_TIME + timedelta(minutes=minute)
        progress_deg = min(1.0, minute / 20.0)   # 0→1 nos primeiros 20 min

        recovering = minute >= FIX_MINUTE
        recovery_progress = (minute - FIX_MINUTE) / (40 - FIX_MINUTE) if recovering else 0.0

        # === Pool active ===
        if not recovering:
            # Ato 1+2: logística de degradação
            logistic = 1.0 / (1.0 + _math.exp(-8.0 * (progress_deg - 0.50)))
            pool_frac = 0.05 + logistic * 0.78 + random.gauss(0, 0.005)
        else:
            # Ato 3: recovery real — max_connections 200→500.
            # O numerador (conexões ativas) cai gradualmente à medida que o backlog
            # de requests pendentes é processado. O denominador salta para 500,
            # então o ratio pool_frac cai imediatamente para ~0.33 e depois para ~0.18.
            peak_active = 0.82 * MAX_POOL_BEFORE      # ~164 conexões no pico
            active_now = peak_active * (1.0 - recovery_progress * 0.45)  # 164 → ~90
            pool_frac = max(0.05, active_now / MAX_POOL_AFTER + random.gauss(0, 0.005))

        pool_frac = max(0.0, min(1.0, pool_frac))

        # 2 readings por minuto
        for sub_sec in (0, 30):
            sub_ts = ts + timedelta(seconds=sub_sec)
            noise = random.gauss(0, 0.008)
            frac = max(0.0, min(1.0, pool_frac + noise))

            corpus.append((sub_ts, MetricPoint(
                timestamp=sub_ts, service="db-pool",
                metric_name="connection_pool_active", value=frac,
            )))

            cpu_peak = 0.22 + progress_deg * 0.40
            cpu = (cpu_peak * (1.0 - recovery_progress * 0.70)) if recovering else cpu_peak
            cpu += random.gauss(0, 0.02)
            corpus.append((sub_ts, MetricPoint(
                timestamp=sub_ts, service="payment-service",
                metric_name="cpu_usage", value=max(0.0, min(1.0, cpu)),
            )))

            mem = 0.38 + progress_deg * 0.22
            if recovering:
                mem *= (1.0 - recovery_progress * 0.30)
            mem += random.gauss(0, 0.01)
            corpus.append((sub_ts, MetricPoint(
                timestamp=sub_ts, service="payment-service",
                metric_name="memory_usage", value=max(0.0, min(1.0, mem)),
            )))

            # Latência
            if not recovering:
                if pool_frac <= 0.5:
                    lat_p95 = BASE_LATENCY_MS + pool_frac * 350.0
                else:
                    lat_p95 = BASE_LATENCY_MS + 175.0 + ((pool_frac - 0.5) ** 2) * 9000.0
            else:
                # Recuperação: latência desce exponencialmente
                peak_lat = BASE_LATENCY_MS + 175.0 + ((0.82 - 0.5) ** 2) * 9000.0
                lat_p95 = BASE_LATENCY_MS + (peak_lat - BASE_LATENCY_MS) * _math.exp(-8.0 * recovery_progress)

            lat_p95 = max(50.0, lat_p95 + random.gauss(0, lat_p95 * 0.06))
            lat_p99 = max(60.0, lat_p95 * 1.45 + random.gauss(0, 25.0))

            corpus.append((sub_ts, MetricPoint(
                timestamp=sub_ts, service="api-gateway",
                metric_name="latency_p95_ms", value=lat_p95,
            )))
            corpus.append((sub_ts, MetricPoint(
                timestamp=sub_ts, service="api-gateway",
                metric_name="latency_p99_ms", value=lat_p99,
            )))

        # === Logs ===
        if not recovering:
            if minute < 8:
                n_logs, phase = random.randint(3, 4), "normal"
            elif minute < 16:
                n_logs, phase = random.randint(6, 10), "early"
            elif minute < 21:
                n_logs, phase = random.randint(15, 22), "active"
            else:
                n_logs, phase = random.randint(25, 35), "incident"
        else:
            # Recovery: volume cai exponencialmente — o fix normaliza os logs rapidamente.
            # exp(-4 * p): p=0.2 → 18/min, p=0.4 → 11/min, p=0.67 → 7/min, p=1.0 → 4/min
            peak_n = 30
            n_logs = max(3, int(peak_n * _math.exp(-4.0 * recovery_progress)) + random.randint(-1, 1))
            phase = "recovery"

        for _ in range(n_logs):
            log_ts = ts + timedelta(seconds=random.randint(0, 59))
            svc = random.choice(services)

            if phase == "normal":
                level, msg = "INFO", random.choice(_TEMPLATES_NORMAL)
            elif phase == "early":
                if random.random() < 0.35:
                    level = "WARNING"
                    msg = f"{random.choice(_TEMPLATES_DEGRADED[:4])} — pool={int(pool_frac*MAX_POOL_BEFORE)}/{MAX_POOL_BEFORE}"
                else:
                    level, msg = "INFO", random.choice(_TEMPLATES_NORMAL)
            elif phase == "active":
                r = random.random()
                if r < 0.55:
                    level = "WARNING"
                    msg = f"{random.choice(_TEMPLATES_DEGRADED[:4])} — pool={int(pool_frac*MAX_POOL_BEFORE)}/{MAX_POOL_BEFORE}"
                elif r < 0.80:
                    level, msg = "ERROR", random.choice(_TEMPLATES_DEGRADED[4:])
                else:
                    level, msg = "INFO", "Conexão obtida do pool — tentativa 2"
            elif phase == "incident":
                if random.random() < 0.82:
                    level, msg = "ERROR", random.choice(_TEMPLATES_INCIDENT)
                else:
                    level = "WARNING"
                    msg = f"Pool timeout crítico — {int(pool_frac*MAX_POOL_BEFORE)}/{MAX_POOL_BEFORE}"
            else:  # recovery
                r = random.random()
                if r < 0.85:
                    level, msg = "INFO", random.choice(_TEMPLATES_RECOVERY)
                else:
                    level, msg = "WARNING", "Pool ainda normalizando — monitorando"

            corpus.append((log_ts, LogEntry(
                timestamp=log_ts, level=level, service=svc, message=msg,
            )))

        # === Spans ===
        n_spans = random.randint(2, 4)
        for span_i in range(n_spans):
            span_ts = ts + timedelta(seconds=random.randint(0, 50))

            if not recovering:
                if pool_frac <= 0.5:
                    root_dur = BASE_LATENCY_MS + pool_frac * 400.0 + random.gauss(0, 15.0)
                else:
                    root_dur = BASE_LATENCY_MS + 200.0 + ((pool_frac - 0.5) ** 2) * 9000.0 + random.gauss(0, 60.0)
                error_prob = max(0.0, (pool_frac - 0.65) * 3.0)
            else:
                peak_dur = BASE_LATENCY_MS + 200.0 + ((0.82 - 0.5) ** 2) * 9000.0
                root_dur = BASE_LATENCY_MS + (peak_dur - BASE_LATENCY_MS) * _math.exp(-8.0 * recovery_progress)
                root_dur += random.gauss(0, 30.0)
                error_prob = max(0.0, (1.0 - recovery_progress * 2.0) * 0.3)

            root_dur = max(10.0, root_dur)
            span_status = "ERROR" if random.random() < error_prob else "OK"
            trace_id = f"tr-{minute:04d}-{span_i}"
            root_id = f"sp-gw-{minute:04d}-{span_i}"

            corpus.append((span_ts, Span(
                trace_id=trace_id, span_id=root_id, parent_span_id=None,
                service="api-gateway", operation="POST /payment",
                start_time=span_ts, duration_ms=root_dur, status=span_status,
            )))

            acquire_dur = max(5.0, root_dur * 0.88 + random.gauss(0, 10.0))
            pay_id = f"sp-pay-{minute:04d}-{span_i}"
            corpus.append((span_ts, Span(
                trace_id=trace_id, span_id=pay_id, parent_span_id=root_id,
                service="payment-service", operation="db.pool.acquire",
                start_time=span_ts + timedelta(milliseconds=5),
                duration_ms=acquire_dur, status=span_status,
            )))

            if pool_frac > 0.40:
                tcp_dur = max(5.0, acquire_dur * 0.75 + random.gauss(0, 12.0))
                db_status = "ERROR" if pool_frac > 0.70 and span_status == "ERROR" else "OK"
                corpus.append((span_ts, Span(
                    trace_id=trace_id,
                    span_id=f"sp-db-{minute:04d}-{span_i}",
                    parent_span_id=pay_id,
                    service="db-pool", operation="tcp.connect",
                    start_time=span_ts + timedelta(milliseconds=12),
                    duration_ms=tcp_dur, status=db_status,
                )))

    corpus.sort(key=lambda x: x[0])
    return corpus


def build_memory_leak_corpus() -> list[CorpusItem]:
    """Constrói corpus para o cenário memory-leak (Java heap leak).

    Simula 60 minutos: memory_used_mb sobe linearmente de 2048 a 7987MB.
    EWMA slope detectável a partir do minuto 3; 3-sigma apenas no minuto 15.
    Usa random.seed(43).
    """
    random.seed(43)
    corpus: list[CorpusItem] = []
    duration_minutes = 60

    for minute in range(duration_minutes):
        ts = _BASE_TIME + timedelta(minutes=minute)
        progress = minute / (duration_minutes - 1)

        # Memória sobe de 2048MB a 7987MB (leak linear)
        mem_mb = 2048.0 + progress * (7987.0 - 2048.0) + random.gauss(0, 8.0)
        corpus.append((ts, MetricPoint(
            timestamp=ts, service="jvm-service",
            metric_name="memory_used_mb", value=max(0.0, mem_mb),
        )))

        # CPU normal (30-40%)
        cpu = 0.30 + random.gauss(0, 0.02) + (0.05 if minute > 45 else 0.0)
        corpus.append((ts, MetricPoint(
            timestamp=ts, service="jvm-service",
            metric_name="cpu_usage", value=max(0.0, min(1.0, cpu)),
        )))

        # Latência levemente elevada a partir do minuto 20
        latency_p95 = 120.0
        if minute >= 20:
            latency_p95 = 120.0 + (minute - 20) * 8.0
        latency_p95 += random.gauss(0, 5.0)
        corpus.append((ts, MetricPoint(
            timestamp=ts, service="jvm-service",
            metric_name="latency_p95_ms", value=max(100.0, latency_p95),
        )))

        # GC logs — frequência aumenta com leak
        if minute < 10:
            level = "INFO"
            msg = f"GC minor collection: freed {random.randint(50, 200)}MB in {random.randint(20, 80)}ms"
        elif minute < 30:
            level = "WARNING"
            msg = f"GC major collection: freed {random.randint(10, 50)}MB — heap pressure increasing ({int(mem_mb)}MB used)"
        else:
            level = "ERROR"
            msg = f"OutOfMemoryError risk: heap {int(mem_mb)}MB/{7987}MB — GC overhead {random.randint(80, 99)}%"

        corpus.append((ts, LogEntry(
            timestamp=ts, level=level, service="jvm-service", message=msg,
        )))

        # Span
        span_dur = 80.0 + (20.0 if minute > 30 else 0.0) + random.gauss(0, 5.0)
        corpus.append((ts, Span(
            trace_id=f"ml-tr-{minute:04d}",
            span_id=f"ml-sp-{minute:04d}",
            parent_span_id=None,
            service="jvm-service",
            operation="GET /api/data",
            start_time=ts,
            duration_ms=max(10.0, span_dur),
            status="OK",
        )))

    corpus.sort(key=lambda x: x[0])
    return corpus


def build_unknown_fault_corpus() -> list[CorpusItem]:
    """Constrói corpus para o cenário unknown-fault (modo de falha não documentado).

    40 minutos de simulação: normal até minuto 5, depois templates de log
    novos (não presentes no baseline) + spikes de latência a partir do minuto 10.
    Usa random.seed(44).
    """
    random.seed(44)
    corpus: list[CorpusItem] = []
    duration_minutes = 40

    # Templates de log conhecidos (baseline)
    known_templates = [
        "Requisição processada com sucesso",
        "Cache miss — buscando no banco",
        "Autenticação validada para user:{uid}",
    ]

    # Templates NOVOS — não estão no baseline (simula falha desconhecida)
    novel_templates = [
        "KERNEL_PANIC: unexpected irq handler at 0x{addr:08x}",
        "FAULT: memory access violation in module core_sched",
        "CRITICAL: hardware interrupt storm — {n} IRQs/s",
        "NMI: non-maskable interrupt received — possible hardware fault",
        "BUG: scheduler state machine entered undefined transition",
    ]

    for minute in range(duration_minutes):
        ts = _BASE_TIME + timedelta(minutes=minute)
        progress = minute / (duration_minutes - 1)

        # CPU normal até falha
        cpu = 0.20 + random.gauss(0, 0.015)
        if minute >= 5:
            cpu += 0.10 + (minute - 5) * 0.005
        corpus.append((ts, MetricPoint(
            timestamp=ts, service="kernel-service",
            metric_name="cpu_usage", value=max(0.0, min(1.0, cpu)),
        )))

        # Latência — spikes a partir do minuto 10
        if minute < 10:
            latency = 60.0 + random.gauss(0, 5.0)
        else:
            spike = random.choice([200.0, 500.0, 1200.0, 2500.0]) if random.random() < 0.6 else 80.0
            latency = spike + random.gauss(0, 10.0)

        corpus.append((ts, MetricPoint(
            timestamp=ts, service="kernel-service",
            metric_name="latency_p95_ms", value=max(40.0, latency),
        )))

        # Logs: normais antes do minuto 5, novos templates depois
        if minute < 5:
            tpl = random.choice(known_templates)
            msg = tpl.format(uid=random.randint(100, 999))
            level = "INFO"
        elif minute < 10:
            # Começa misturando templates desconhecidos
            if random.random() < 0.5:
                tpl = random.choice(novel_templates)
                msg = tpl.format(addr=random.randint(0, 0xFFFFFFFF), n=random.randint(1000, 50000))
                level = "WARNING"
            else:
                tpl = random.choice(known_templates)
                msg = tpl.format(uid=random.randint(100, 999))
                level = "INFO"
        else:
            # Predominantemente templates novos + erros
            tpl = random.choice(novel_templates)
            msg = tpl.format(addr=random.randint(0, 0xFFFFFFFF), n=random.randint(5000, 100000))
            level = "ERROR"

        corpus.append((ts, LogEntry(
            timestamp=ts, level=level, service="kernel-service", message=msg,
        )))

        # Spans — timeouts irregulares
        dur = 60.0 if minute < 10 else (random.choice([500.0, 1500.0, 3000.0]) if random.random() < 0.5 else 100.0)
        corpus.append((ts, Span(
            trace_id=f"uf-tr-{minute:04d}",
            span_id=f"uf-sp-{minute:04d}",
            parent_span_id=None,
            service="kernel-service",
            operation="SYS /irq_handler",
            start_time=ts,
            duration_ms=max(10.0, dur + random.gauss(0, 5.0)),
            status="OK" if minute < 10 else "ERROR",
        )))

    corpus.sort(key=lambda x: x[0])
    return corpus


# Registro de todos os cenários disponíveis
SCENARIOS: dict[str, dict] = {
    "payment-pool-exhaustion": {
        "name": "payment-pool-exhaustion",
        "display_name": "Payment Pool Exhaustion",
        "description": (
            "Simulação de esgotamento do pool de conexões no serviço de pagamento. "
            "connection_pool_active sobe de 5% a 86% em 32 minutos com curva logística."
        ),
        "duration_sim_minutes": 32,
        "requires_llm": False,
        "corpus_builder": build_payment_pool_corpus,
    },
    "payment-pool-recovery": {
        "name": "payment-pool-recovery",
        "display_name": "Pool Exhaustion + Recovery",
        "description": (
            "Esgotamento do pool seguido de recuperação operacional. "
            "Ops aumenta max_connections (200→500) no minuto 25. "
            "Demonstra detecção de piora E melhora sem falsos positivos."
        ),
        "duration_sim_minutes": 40,
        "requires_llm": False,
        "corpus_builder": build_payment_pool_recovery_corpus,
    },
    "memory-leak": {
        "name": "memory-leak",
        "display_name": "JVM Memory Leak",
        "description": (
            "Simulação de memory leak no heap Java. "
            "memory_used_mb cresce linearmente de 2048MB a 7987MB em 60 minutos. "
            "Sinal EWMA detectável no minuto 3 vs 3-sigma apenas no minuto 15."
        ),
        "duration_sim_minutes": 60,
        "requires_llm": False,
        "corpus_builder": build_memory_leak_corpus,
        # Leak gradual: score estabiliza em ~38-54; limiares padrão 60/80 nunca seriam atingidos
        "risk_warning_threshold": 35.0,
        "risk_alert_threshold": 50.0,
        "ewma_threshold": 7200.0,  # 90% de 7987MB para cálculo de ETA correto
    },
    "unknown-fault": {
        "name": "unknown-fault",
        "display_name": "Unknown Fault Mode",
        "description": (
            "Modo de falha não documentado: templates de log nunca vistos no baseline "
            "aparecem no minuto 5, seguidos por spikes de latência no minuto 10."
        ),
        "duration_sim_minutes": 40,
        "requires_llm": False,
        "corpus_builder": build_unknown_fault_corpus,
    },
}


def get_scenarios_list() -> list[dict]:
    """Retorna lista de metadados dos cenários (sem corpus_builder) para a API REST."""
    return [
        {k: v for k, v in scenario.items() if k != "corpus_builder"}
        for scenario in SCENARIOS.values()
    ]
