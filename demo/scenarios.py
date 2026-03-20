from __future__ import annotations
"""
Corpora de cenários de demonstração: dados pré-gerados determinísticos
para replay no motor de simulação.

Cada corpus é uma lista de (sim_timestamp, item) onde item pode ser
LogEntry, MetricPoint ou Span. Todos usam seeds fixas para reprodutibilidade.
"""

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
    """Constrói corpus determinístico para o cenário payment-pool-exhaustion.

    Simula 32 minutos com connection_pool_active subindo de 5% a 86%.
    Usa random.seed(42) para reprodutibilidade.
    """
    random.seed(42)
    corpus: list[CorpusItem] = []
    duration_minutes = 32

    # Serviços participantes
    services = ["payment-service", "db-pool", "api-gateway", "cache"]
    log_templates_normal = [
        "Processando transação id={txid}",
        "Conexão obtida do pool em {ms}ms",
        "Cache hit para chave user:{uid}",
        "Requisição completada em {ms}ms",
    ]
    log_templates_degraded = [
        "Pool timeout após {ms}ms — tentando reconectar",
        "Conexão recusada: pool_active={pct}%",
        "Erro ao adquirir conexão: PoolExhaustedException",
        "Retry #{n} — aguardando conexão disponível",
        "WARN: connection_pool_active={pct}% acima do limiar",
    ]

    for minute in range(duration_minutes):
        ts = _BASE_TIME + timedelta(minutes=minute)
        progress = minute / (duration_minutes - 1)  # 0.0 → 1.0

        # Métrica: connection_pool_active sobe de 5% a 86%
        pool_active = 0.05 + progress * 0.81 + random.gauss(0, 0.005)
        pool_active = max(0.0, min(1.0, pool_active))
        corpus.append((ts, MetricPoint(
            timestamp=ts, service="db-pool",
            metric_name="connection_pool_active", value=pool_active,
        )))

        # Métricas auxiliares
        cpu = 0.25 + progress * 0.30 + random.gauss(0, 0.02)
        corpus.append((ts, MetricPoint(
            timestamp=ts, service="payment-service",
            metric_name="cpu_usage", value=max(0.0, min(1.0, cpu)),
        )))

        mem = 0.40 + progress * 0.25 + random.gauss(0, 0.01)
        corpus.append((ts, MetricPoint(
            timestamp=ts, service="payment-service",
            metric_name="memory_usage", value=max(0.0, min(1.0, mem)),
        )))

        # Latência p95 (ms) — sobe com degradação
        latency_p95 = 80.0 + progress * 800.0 + random.gauss(0, 10.0)
        corpus.append((ts, MetricPoint(
            timestamp=ts, service="api-gateway",
            metric_name="latency_p95_ms", value=max(50.0, latency_p95),
        )))

        latency_p99 = latency_p95 * 1.3 + random.gauss(0, 15.0)
        corpus.append((ts, MetricPoint(
            timestamp=ts, service="api-gateway",
            metric_name="latency_p99_ms", value=max(60.0, latency_p99),
        )))

        # Logs — templates degradados aparecem após minuto 8
        service = random.choice(services)
        if minute < 8:
            tpl = random.choice(log_templates_normal)
            msg = tpl.format(txid=random.randint(1000, 9999),
                             ms=random.randint(5, 30),
                             uid=random.randint(100, 999))
            level = "INFO"
        elif minute < 16:
            # Mistura normal e degradado
            if random.random() < 0.4:
                tpl = random.choice(log_templates_degraded)
                msg = tpl.format(ms=random.randint(500, 2000),
                                 pct=int(pool_active * 100),
                                 n=random.randint(1, 3))
                level = "WARNING"
            else:
                tpl = random.choice(log_templates_normal)
                msg = tpl.format(txid=random.randint(1000, 9999),
                                 ms=random.randint(50, 300),
                                 uid=random.randint(100, 999))
                level = "INFO"
        else:
            # Predominantemente degradado
            tpl = random.choice(log_templates_degraded)
            msg = tpl.format(ms=random.randint(2000, 5000),
                             pct=int(pool_active * 100),
                             n=random.randint(3, 10))
            level = "ERROR" if minute > 22 else "WARNING"

        corpus.append((ts, LogEntry(
            timestamp=ts, level=level, service=service, message=msg,
        )))

        # Spans de trace
        span_id = f"sp-{minute:04d}-{random.randint(1000, 9999)}"
        duration = 50.0 + progress * 3000.0 + random.gauss(0, 20.0)
        corpus.append((ts, Span(
            trace_id=f"tr-{minute:04d}",
            span_id=span_id,
            parent_span_id=None,
            service="api-gateway",
            operation="POST /payment",
            start_time=ts,
            duration_ms=max(10.0, duration),
            status="OK" if minute < 20 else "ERROR",
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
            "connection_pool_active sobe de 5% a 86% em 32 minutos."
        ),
        "duration_sim_minutes": 32,
        "requires_llm": False,
        "corpus_builder": build_payment_pool_corpus,
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
