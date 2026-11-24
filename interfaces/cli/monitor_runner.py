"""Runner do modo ``--monitor`` (Vigia Barato operável pela CLI, OP2).

Camada barata (``ContinuousMonitor``, zero token) + escalada para o diagnóstico
profundo V1 (``EscalationHook``). Fail-closed: o monitor só PROPÕE (TNR sempre
``simulated``); escalada ⇒ aprovação humana obrigatória (exit 3).

Fontes: ``csv://<arquivo>`` é *replay* (relógio simulado, sem espera real);
``prometheus://<host:porta>`` usa relógio e sleep reais.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from application.ports.telemetry_sources import MetricsSource
from data import hpa_saturation_scenario as hpa
from interfaces.cli.console import console
from interfaces.cli.exit_codes import EXIT_APPROVAL_NEEDED, EXIT_ERROR, EXIT_SUCCESS
from pipeline.continuous_monitor import (
    ContinuousMonitor,
    EscalationHook,
    MonitorTick,
)

CSV_SCHEME = "csv://"
PROM_SCHEME = "prometheus://"
# Mesmo horizonte do backtest (scripts/backtest_continuous.py HPA_ESCALATE_ETA_MIN).
HPA_ESCALATE_ETA_MIN = 60.0
# Replay começa com janela já preenchida (como o backtest: START_TIME + 30min).
REPLAY_WARMUP = timedelta(minutes=30)


class MonitorSourceError(Exception):
    """Fonte inválida/inacessível (vira EXIT_ERROR)."""


def _build_source(spec: str) -> MetricsSource:
    if spec.startswith(CSV_SCHEME):
        from tools.csv_metrics_source import CsvMetricsSource

        path = Path(spec[len(CSV_SCHEME) :])
        try:
            return CsvMetricsSource(path)
        except (OSError, ValueError, KeyError) as exc:
            raise MonitorSourceError(f"CSV inválido ({path}): {exc}") from exc
    if spec.startswith(PROM_SCHEME):
        from tools.prometheus_connector import PrometheusMetricsSource

        target = spec[len(PROM_SCHEME) :]
        if not target:
            raise MonitorSourceError("prometheus:// sem host")
        base = target if "://" in target else f"http://{target}"
        try:
            return PrometheusMetricsSource(base)
        except Exception as exc:
            raise MonitorSourceError(f"Prometheus inválido: {exc}") from exc
    raise MonitorSourceError(
        f"--source inválido: {spec!r} (use csv://<path> ou prometheus://<url>)"
    )


class _PrintingMonitor(ContinuousMonitor):
    """Imprime cada tick (score, nível, ETA, saturação, escalada)."""

    def tick(self, now: datetime) -> MonitorTick:
        result = super().tick(now)
        eta = f"{result.eta_minutes:.0f}min" if result.eta_minutes is not None else "-"
        sat = "sim" if result.saturation is not None else "nao"
        console.print(
            f"tick {result.timestamp.isoformat()} score={result.score:.2f} "
            f"level={result.level or '-'} eta={eta} saturacao={sat} "
            f"escalar={'SIM' if result.should_escalate else 'nao'}",
            markup=False,
            highlight=False,
        )
        return result


def _replay_times(source: MetricsSource, interval: float) -> list[datetime]:
    """Instantes do replay CSV: de first+warmup até o último ponto."""
    far = datetime(9999, 1, 1, tzinfo=timezone.utc)
    rows = source.query("", datetime(1970, 1, 1, tzinfo=timezone.utc), far)
    stamps = sorted(
        ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        for ts in (datetime.fromisoformat(str(r["timestamp"])) for r in rows)
    )
    if not stamps:
        raise MonitorSourceError("CSV sem pontos")
    start = stamps[0] + REPLAY_WARMUP
    n = max(1, math.floor((stamps[-1] - start).total_seconds() / interval) + 1)
    return [start + timedelta(seconds=interval * i) for i in range(n)]


def _is_hpa(source: MetricsSource) -> bool:
    far = datetime(9999, 1, 1, tzinfo=timezone.utc)
    rows = source.query(
        hpa.METRIC_MEMORY, datetime(1970, 1, 1, tzinfo=timezone.utc), far
    )
    return bool(rows)


def run_monitor(args: Any, *, sleep: Callable[[float], None] | None = None) -> int:
    """Executa o monitor. 0 = limpo, 3 = escalada (aprovação humana), 1 = falha."""
    try:
        source = _build_source(args.source)
        replay = args.source.startswith(CSV_SCHEME)
        times = _replay_times(source, args.interval) if replay else []
        from app.monitor_setup import make_blueprint_diagnose
        from config.loader import load_config
        from interfaces.http.notifications.slack import build_monitor_slack_notifier

        cfg = load_config(getattr(args, "config", None))
        hook = EscalationHook(
            make_blueprint_diagnose(cfg, no_llm=args.no_llm),
            notify=build_monitor_slack_notifier().on_event,
            service=args.monitor_service,
        )
        kwargs: dict[str, Any] = {}
        if replay:
            it = iter(times)
            kwargs["clock"] = lambda: next(it)
            kwargs["sleep"] = lambda _s: None  # replay: sem espera real
        elif sleep is not None:
            kwargs["sleep"] = sleep
        if replay and _is_hpa(source):
            req = hpa.MEMORY_REQUEST_BYTES
            kwargs.update(
                metrics=[hpa.METRIC_MEMORY],
                ewma_threshold=0.9 * req,
                escalate_eta_minutes=HPA_ESCALATE_ETA_MIN,
                sustained_levels={
                    hpa.METRIC_MEMORY: (hpa.HPA_TARGET_UTILIZATION * req, req)
                },
            )
        monitor = _PrintingMonitor(
            source, interval_seconds=args.interval, escalation=hook, **kwargs
        )
        ticks = args.ticks if args.ticks is not None else (len(times) or None)
        if replay and ticks is not None:
            ticks = min(ticks, len(times))
        if ticks is not None:
            monitor.run_for(ticks)
        else:
            monitor.run_forever()  # pragma: no cover - loop infinito
    except MonitorSourceError as exc:
        console.print(f"[red]monitor: {exc}[/red]")
        return EXIT_ERROR
    except KeyboardInterrupt:  # pragma: no cover - encerramento manual
        pass
    except Exception as exc:  # fail-closed: falha de fonte/execução ≠ "limpo"
        console.print(f"[red]monitor falhou: {exc}[/red]", markup=True)
        return EXIT_ERROR

    if monitor.escalations:
        first = monitor.escalations[0]
        console.print(
            f"Escaladas: {len(monitor.escalations)} (primeira em "
            f"{first.tick.timestamp.isoformat()}); proposta SIMULADA — "
            "aprovação humana obrigatória.",
            markup=False,
        )
        return EXIT_APPROVAL_NEEDED
    console.print("Monitor concluído sem escalada.", markup=False)
    return EXIT_SUCCESS
