#!/usr/bin/env python3
"""Demo do Vigia Barato (monitor contínuo) sobre o cenário HPA — sem infra real.

Gera o CSV do cenário em diretório temporário, reproduz a linha do tempo com
relógio simulado (sem sleep real) de ``START_TIME+30min`` até ``RESTART_AT`` e
imprime: ticks, primeiro alerta, lead time antes de ``MAXED_OUT_AT`` e a
escalada (proposta SIMULADA; nada é executado, aprovação humana obrigatória).

Uso:
    .venv/bin/python scripts/run_continuous_demo.py [--seed 42]
        [--source csv://<arquivo>] [--workdir <dir>] [--llm]

Exit: 0 sem escalada, 3 escalada (aprovação humana), 1 falha/fonte inválida.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data import hpa_saturation_scenario as hpa  # noqa: E402
from domain.incident_state import IncidentState  # noqa: E402
from interfaces.cli.exit_codes import (  # noqa: E402
    EXIT_APPROVAL_NEEDED,
    EXIT_ERROR,
    EXIT_SUCCESS,
)
from interfaces.cli.monitor_runner import (  # noqa: E402
    HPA_ESCALATE_ETA_MIN,
    REPLAY_WARMUP,
    MonitorSourceError,
    _build_source,
)
from pipeline.continuous_monitor import (  # noqa: E402
    ContinuousMonitor,
    EscalationHook,
    MonitorTick,
)

Diagnose = Callable[[IncidentState], IncidentState]
STEP_SECONDS = 60


def _default_diagnose(no_llm: bool) -> Diagnose:
    from app.monitor_setup import make_blueprint_diagnose
    from config.loader import load_config

    return make_blueprint_diagnose(load_config(None), no_llm=no_llm)


def _minutes(a: datetime, b: datetime) -> float:
    return (b - a).total_seconds() / 60


def run_demo(
    *,
    source: str | None = None,
    seed: int = 42,
    workdir: Path | None = None,
    diagnose: Diagnose | None = None,
    no_llm: bool = True,
) -> dict[str, Any]:
    """Roda o replay e devolve o resumo (lead time, escalada, executed_infra)."""
    if source is None:
        base = workdir or Path(tempfile.mkdtemp(prefix="vigia-demo-"))
        csv_path = hpa.export_csv(base / f"hpa_{seed}.csv", seed=seed)
        source = f"csv://{csv_path}"
    src = _build_source(source)
    req = hpa.MEMORY_REQUEST_BYTES
    hook = EscalationHook(diagnose or _default_diagnose(no_llm), service="hpa-demo")
    times: list[datetime] = []
    t = hpa.START_TIME + REPLAY_WARMUP
    while t <= hpa.RESTART_AT:
        times.append(t)
        t += timedelta(seconds=STEP_SECONDS)
    it = iter(times)
    monitor = ContinuousMonitor(
        src,
        metrics=[hpa.METRIC_MEMORY],
        interval_seconds=STEP_SECONDS,
        ewma_threshold=0.9 * req,
        escalate_eta_minutes=HPA_ESCALATE_ETA_MIN,
        sustained_levels={hpa.METRIC_MEMORY: (hpa.HPA_TARGET_UTILIZATION * req, req)},
        clock=lambda: next(it),
        sleep=lambda _s: None,  # sleep zero: replay acelerado
        escalation=hook,
    )
    ticks: list[MonitorTick] = monitor.run_for(len(times))
    failure = hpa.MAXED_OUT_AT
    alerts = [x for x in ticks if x.level is not None and x.timestamp < failure]
    first_alert = alerts[0].timestamp if alerts else None
    escalations = monitor.escalations
    first_esc = escalations[0].tick.timestamp if escalations else None
    anchor = first_esc or first_alert
    return {
        "ticks": len(ticks),
        "start": times[0],
        "end": times[-1],
        "maxed_out_at": failure,
        "first_alert_at": first_alert,
        "first_escalation_at": first_esc,
        "lead_time_min": _minutes(anchor, failure) if anchor else None,
        "alert_lead_time_min": _minutes(first_alert, failure) if first_alert else None,
        "escalations": len(escalations),
        "executed_infra": False,
        "verification_mode": "simulated",
        "timeline": ticks,
    }


def _fmt(ts: datetime | None) -> str:
    if ts is None:
        return "-"
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).strftime(
        "%d/%m %H:%M"
    )


def _print(result: dict[str, Any]) -> None:
    print("== Vigia Barato — demo contínua (HPA saturado, dados simulados) ==")
    print(
        f"janela: {_fmt(result['start'])} -> {_fmt(result['end'])}  ticks={result['ticks']}"
    )
    prev: tuple[str | None, bool] | None = None
    for x in result["timeline"]:
        cur = (x.level, x.should_escalate)
        if cur != prev:  # só transições: a linha do tempo fica legível
            eta = f"{x.eta_minutes:.0f}min" if x.eta_minutes is not None else "-"
            print(
                f"  {_fmt(x.timestamp)}  nivel={x.level or '-'}  eta={eta}  "
                f"escalar={'SIM' if x.should_escalate else 'nao'}"
            )
        prev = cur
    print(f"MAXED_OUT_AT:       {_fmt(result['maxed_out_at'])}")
    print(f"primeiro alerta:    {_fmt(result['first_alert_at'])}")
    print(f"primeira escalada:  {_fmt(result['first_escalation_at'])}")
    lead = result["lead_time_min"]
    print(
        f"lead time antes de MAXED_OUT_AT: {lead:.0f} min"
        if lead is not None
        else "lead time: n/a (sem alerta)"
    )
    print(
        f"escaladas={result['escalations']}  proposta verification_mode="
        f"{result['verification_mode']} (simulated)  "
        f"executed_infra={result['executed_infra']}  aprovação humana obrigatória"
    )


def main(argv: Sequence[str] | None = None, *, diagnose: Diagnose | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--source", help="csv://<arquivo> (padrão: CSV HPA gerado)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--workdir", type=Path, help="diretório do CSV gerado (padrão: tmp)")
    p.add_argument(
        "--llm", action="store_true", help="diagnóstico com LLM (padrão: --no-llm)"
    )
    args = p.parse_args(argv)
    if args.source is not None and not args.source.startswith("csv://"):
        print("demo: --source deve ser csv://<arquivo>", file=sys.stderr)
        return EXIT_ERROR
    try:
        result = run_demo(
            source=args.source,
            seed=args.seed,
            workdir=args.workdir,
            diagnose=diagnose,
            no_llm=not args.llm,
        )
    except MonitorSourceError as exc:
        print(f"demo: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except Exception as exc:  # fail-closed: falha ≠ "limpo"
        print(f"demo falhou: {exc}", file=sys.stderr)
        return EXIT_ERROR
    _print(result)
    return EXIT_APPROVAL_NEEDED if result["escalations"] else EXIT_SUCCESS


if __name__ == "__main__":
    sys.exit(main())
