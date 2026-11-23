"""Vigia Barato — camada barata de monitoramento contínuo (VB2).

A cada ``tick`` consulta uma janela de métricas numa ``MetricsSource``, monta
``MetricPoint`` por métrica e reaproveita a matemática já existente (a mesma
cadeia de ``interfaces/demo/analyzer.py``): ``compute_ewma_slope`` →
``compute_risk_score`` (+ ``detect_saturation``) → ``RiskTracker.update`` →
``get_event_level``. Aplica os portões D3 de ``predictive_stage`` (série curta,
efeito relativo mínimo) para flat/ruído não alertarem.

Camada barata: NUNCA chama LLM (não importa ``adapters.llm``) e não executa
nada em infra — somente leitura de métricas.

VB3 — escalada: quando ``should_escalate``, o ``EscalationHook`` injetado monta
um ``IncidentState`` para serviço+janela e chama o pipeline de diagnóstico
(camada profunda, injetada: ``make_blueprint_diagnose`` = V1 ``BlueprintEngine``)
e emite o aviso proativo ("vai estourar em ~X min" / "saturação no teto").
O resultado é PROPOSTA + postmortem: nenhum caminho aqui executa infra
(fail-closed; aprovação humana continua obrigatória). A previsão é sempre
``verification_mode="simulated"``.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from application.ports.telemetry_sources import MetricsSource
from core.lifecycle import LifecycleEvent
from data.models import MetricPoint
from domain.alert import NormalizedAlert
from domain.incident_state import IncidentState
from pipeline.metric_predictor import compute_ewma_slope
from pipeline.predictive_stage import (
    MIN_RELATIVE_EFFECT,
    MIN_SERIES_POINTS,
    _relative_effect,
)
from pipeline.risk_scorer import (
    MetricTrendSignal,
    compute_risk_score,
    get_event_level,
    relative_slope,
)
from pipeline.risk_tracker import RiskTracker
from pipeline.saturation_detector import SaturationSignal, detect_saturation

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_MINUTES = 60
DEFAULT_ESCALATE_ETA_MINUTES = 30.0
DEFAULT_EWMA_THRESHOLD = 0.90  # mesmo default do DemoAnalyzer (métricas normalizadas)


@dataclass(frozen=True)
class MonitorTick:
    """Resultado de um tick do monitor."""

    timestamp: datetime
    score: float
    level: str | None  # "prediction.alert" | "prediction.warning" | None
    eta_minutes: float | None
    saturation: SaturationSignal | None
    should_escalate: bool


DEFAULT_REARM_MINUTES = 30.0
VERIFICATION_MODE = "simulated"


@dataclass(frozen=True)
class EscalationResult:
    """Resultado de uma escalada: proposta + postmortem, NUNCA execução."""

    tick: MonitorTick
    state: IncidentState
    warning_sent: bool
    error: str | None = None
    executed_infra: bool = False  # invariante: a camada barata não executa infra
    requires_human_approval: bool = True
    verification_mode: str = VERIFICATION_MODE


class EscalationHook:
    """Escala da camada barata para a profunda (diagnóstico) + avisa proativamente.

    ``diagnose``: ``IncidentState -> IncidentState`` (ex.: ``make_blueprint_diagnose``);
    ``notify``: recebe um ``LifecycleEvent`` ``prediction.*`` (ex.: Slack dry-run);
    ``audit``: recebe um dict de auditoria (sempre ``verification_mode=simulated``).
    Dedup: um incidente aberto gera UM diagnóstico (o token da camada profunda é
    o recurso caro); re-arma quando o nível volta ao normal (``reset``) ou após
    ``rearm_minutes`` ainda escalando.
    """

    def __init__(
        self,
        diagnose: Callable[[IncidentState], IncidentState],
        *,
        notify: Callable[[LifecycleEvent], Any] | None = None,
        audit: Callable[[dict[str, Any]], None] | None = None,
        service: str = "unknown",
        rearm_minutes: float = DEFAULT_REARM_MINUTES,
    ) -> None:
        self.diagnose = diagnose
        self.notify = notify
        self.audit = audit
        self.service = service
        self.rearm = timedelta(minutes=rearm_minutes)
        self._last: datetime | None = None

    def reset(self) -> None:
        self._last = None

    def __call__(
        self, tick: MonitorTick, window: tuple[datetime, datetime]
    ) -> EscalationResult | None:
        if not tick.should_escalate:
            self.reset()  # incidente encerrado → próxima subida escala de novo
            return None
        if self._last is not None and tick.timestamp - self._last < self.rearm:
            return None
        self._last = tick.timestamp
        state = self._build_state(tick, window)
        warning_sent = self._warn(tick, state)
        error: str | None = None
        try:
            state = self.diagnose(state)
        except Exception as exc:  # camada profunda fora não derruba o vigia
            logger.exception("continuous_monitor.diagnose_failed")
            error = str(exc)
        result = EscalationResult(tick, state, warning_sent, error)
        self._audit(result)
        return result

    def _build_state(
        self, tick: MonitorTick, window: tuple[datetime, datetime]
    ) -> IncidentState:
        ts = (
            tick.timestamp
            if tick.timestamp.tzinfo
            else tick.timestamp.replace(tzinfo=timezone.utc)
        )
        cid = f"inc-{ts.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
        sat = tick.saturation is not None
        eta = f"~{tick.eta_minutes:.0f} min" if tick.eta_minutes is not None else "?"
        alert = NormalizedAlert(
            source="continuous_monitor",
            source_id=f"monitor-{cid}",
            correlation_id=cid,
            title=(
                f"Saturação no teto em {self.service}"
                if sat
                else f"Previsão de estouro em {eta} — {self.service}"
            ),
            description=(
                f"Vigia barato: nível {tick.level}, score {tick.score:.0f}, "
                f"ETA {eta}. Previsão {VERIFICATION_MODE}."
            ),
            severity="critical" if tick.level == "prediction.alert" else "warning",
            alert_type="PredictiveSaturation" if sat else "PredictiveTrend",
            timestamp=ts,
            detection_timestamp=ts,
            service=self.service,
            tags={"verification_mode": VERIFICATION_MODE},
        )
        return IncidentState(
            incident_id=cid,
            correlation_id=cid,
            alert=alert,
            received_at=ts,
            started_at=ts,
            no_llm=False,
            metadata={
                "monitor_window": {
                    "start": window[0].isoformat(),
                    "end": window[1].isoformat(),
                },
                "monitor_tick": {
                    "score": tick.score,
                    "level": tick.level,
                    "eta_minutes": tick.eta_minutes,
                    "saturation": sat,
                },
                "verification_mode": VERIFICATION_MODE,
            },
        )

    def _warn(self, tick: MonitorTick, state: IncidentState) -> bool:
        if self.notify is None:
            return False
        event = LifecycleEvent(
            event_type=tick.level or "prediction.warning",
            node_name=None,
            correlation_id=state.correlation_id,
            timestamp=tick.timestamp,
            data={
                "event_level": tick.level,
                "risk_score": tick.score,
                "eta_minutes": tick.eta_minutes,
                "saturation": tick.saturation is not None,
                "service": self.service,
                "signals": ["saturation"] if tick.saturation else ["trend"],
                "scenario": state.alert.title,
                "verification_mode": VERIFICATION_MODE,
            },
        )
        try:
            out = self.notify(event)
            if inspect.isawaitable(out):
                asyncio.run(_await(out))
            return True
        except Exception:  # Slack nunca bloqueia o diagnóstico
            logger.warning("continuous_monitor.notify_failed", exc_info=True)
            return False

    def _audit(self, result: EscalationResult) -> None:
        record = {
            "event": "continuous_monitor.escalation",
            "correlation_id": result.state.correlation_id,
            "service": self.service,
            "level": result.tick.level,
            "eta_minutes": result.tick.eta_minutes,
            "verification_mode": result.verification_mode,
            "executed_infra": result.executed_infra,
            "requires_human_approval": result.requires_human_approval,
            "error": result.error,
        }
        logger.info("continuous_monitor.escalation %s", record)
        if self.audit is not None:
            try:
                self.audit(record)
            except Exception:
                logger.warning("continuous_monitor.audit_failed", exc_info=True)


async def _await(awaitable: Any) -> Any:
    return await awaitable


class ContinuousMonitor:
    """Loop barato: ``tick(now)`` é determinístico e testável sem sleep."""

    def __init__(
        self,
        source: MetricsSource,
        *,
        interval_seconds: float = 60,
        ewma_threshold: float | None = DEFAULT_EWMA_THRESHOLD,
        escalate_eta_minutes: float = DEFAULT_ESCALATE_ETA_MINUTES,
        tracker: RiskTracker | None = None,
        metrics: list[str] | None = None,
        ceilings: dict[str, float] | None = None,
        window_minutes: float = DEFAULT_WINDOW_MINUTES,
        step_seconds: int = 60,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] | None = None,
        escalation: EscalationHook | None = None,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds deve ser > 0")
        self.source = source
        self.interval_seconds = interval_seconds
        self.ewma_threshold = ewma_threshold
        self.escalate_eta_minutes = escalate_eta_minutes
        self.tracker = tracker if tracker is not None else RiskTracker()
        self.metrics = list(metrics) if metrics else [""]  # "" = todas (CSV)
        self.ceilings = dict(ceilings or {})
        self.window = timedelta(minutes=window_minutes)
        self.step_seconds = step_seconds
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.history: list[MonitorTick] = []
        self.escalation = escalation
        self.escalations: list[EscalationResult] = []

    def _points(self, now: datetime) -> dict[str, list[MetricPoint]]:
        by_name: dict[str, list[MetricPoint]] = {}
        for query in self.metrics:
            rows = self.source.query(
                query, now - self.window, now, step_seconds=self.step_seconds
            )
            for r in rows:
                ts = r["timestamp"]
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                by_name.setdefault(r["metric"], []).append(
                    MetricPoint(ts, r.get("service", ""), r["metric"], r["value"])
                )
        for pts in by_name.values():
            pts.sort(key=lambda p: p.timestamp)
        return by_name

    def tick(self, now: datetime) -> MonitorTick:
        """Avalia a janela terminada em ``now`` (sem sleep, sem relógio)."""
        best: MetricTrendSignal | None = None
        saturation: SaturationSignal | None = None
        for name, points in self._points(now).items():
            if len(points) < MIN_SERIES_POINTS:
                continue
            values = [p.value for p in points]
            sat = detect_saturation(points, self.ceilings.get(name))
            if sat is not None and (
                saturation is None
                or sat.sustained_samples > saturation.sustained_samples
            ):
                saturation = sat
            if _relative_effect(values) < MIN_RELATIVE_EFFECT:
                continue  # flat/ruído (D3)
            slope, eta = compute_ewma_slope(
                points, span=10, threshold=self.ewma_threshold
            )
            # ETA dentro do horizonte de escalada = "vai cruzar o limiar em
            # breve": sinal forte de tendência (equivale a ponto de mudança).
            imminent = (
                slope > 0 and eta is not None and eta <= self.escalate_eta_minutes
            )
            if best is None or slope > best.slope:
                best = MetricTrendSignal(
                    slope=slope,
                    eta_minutes=eta,
                    change_point_detected=imminent,
                    relative_slope=relative_slope(slope, sum(values) / len(values)),
                )
        raw = compute_risk_score(
            best, None, None, use_relative_slope=True, saturation_signal=saturation
        )
        score = self.tracker.update(raw)
        level = get_event_level(score)
        eta = best.eta_minutes if best else None
        should_escalate = level is not None and (
            level == "prediction.alert"
            or saturation is not None
            or (eta is not None and eta <= self.escalate_eta_minutes)
        )
        result = MonitorTick(now, score, level, eta, saturation, should_escalate)
        self.history.append(result)
        if self.escalation is not None:
            try:
                esc = self.escalation(result, (now - self.window, now))
            except Exception:  # a escalada nunca derruba o tick barato
                logger.exception("continuous_monitor.escalation_failed")
                esc = None
            if esc is not None:
                self.escalations.append(esc)
        return result

    async def atick(self, now: datetime) -> MonitorTick:
        """``tick`` sem bloquear o event loop (diagnóstico roda em thread)."""
        return await asyncio.to_thread(self.tick, now)

    def run_for(self, ticks: int) -> list[MonitorTick]:
        """Executa ``ticks`` iterações com ``sleep(interval_seconds)`` entre elas."""
        out: list[MonitorTick] = []
        for i in range(ticks):
            out.append(self.tick(self._clock()))
            if i < ticks - 1:
                self._sleep(self.interval_seconds)
        return out

    def run_forever(self) -> None:  # pragma: no cover - loop infinito
        while True:
            try:
                self.tick(self._clock())
            except Exception:  # o vigia não pode morrer por uma falha de fonte
                logger.exception("continuous_monitor.tick_failed")
            self._sleep(self.interval_seconds)
