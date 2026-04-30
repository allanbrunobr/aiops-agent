from __future__ import annotations
"""
Motor de cenário: máquina de estado que combina ScenarioPlayer, DemoAnalyzer
e transições de fase para produzir TickFrames para o dashboard.

Suporta modo de comparação (dois motores simultâneos: preditivo vs tradicional).
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from data.models import LogEntry, MetricPoint, Span
from interfaces.demo.analyzer import DemoAnalyzer
from interfaces.demo.player import ScenarioPlayer
from interfaces.demo.schemas import DemoPhase, TickFrame
from interfaces.demo.scenarios import CorpusItem, DemoScenarioConfig


@dataclass
class _PhaseTransition:
    """Registro de transição de fase com timestamp wall-clock."""
    from_phase: str
    to_phase: str
    wall_time: float  # time.monotonic()
    sim_time: str     # ISO timestamp simulado


class ScenarioEngine:
    """Motor principal de cenário de demonstração.

    Mantém o estado da fase, acumula dados do corpus e gera TickFrames.
    Suporta modo de comparação (enable_prediction=False para painel Traditional).
    """

    def __init__(
        self,
        corpus: list[CorpusItem],
        scenario_name: str,
        config: DemoScenarioConfig | None = None,
    ) -> None:
        cfg = config or DemoScenarioConfig()

        self.scenario_name = scenario_name
        self.config = cfg

        # Player e analisador preditivo (painel esquerdo / modo normal)
        self._player = ScenarioPlayer(corpus, speed_factor=cfg.speed_factor)
        self._analyzer = DemoAnalyzer(enable_prediction=cfg.enable_prediction, ewma_threshold=cfg.ewma_threshold)

        # Buffers acumulados para análise
        self._logs: list[LogEntry] = []
        self._metrics: list[MetricPoint] = []
        self._spans: list[Span] = []

        # Estado de fase
        self._phase = DemoPhase.IDLE
        self._phase_transitions: list[_PhaseTransition] = []
        self._phase_entry_wall: float = 0.0   # wall-clock da entrada na fase atual
        self._last_tick_wall: float = 0.0

        # Estado para postmortem
        self.last_postmortem: str | None = None
        self._warning_wall_time: float | None = None  # quando left atingiu WARNING

        # Estado para comparação (motor tradicional paralelo)
        self._comparison_active: bool = False
        self._trad_player: ScenarioPlayer | None = None
        self._trad_analyzer: DemoAnalyzer | None = None
        self._trad_logs: list[LogEntry] = []
        self._trad_metrics: list[MetricPoint] = []
        self._trad_spans: list[Span] = []
        self._trad_phase = DemoPhase.IDLE
        self._trad_warning_wall_time: float | None = None

        # Lead time acumulado (segundos)
        self._lead_time_seconds: float | None = None

    # ------------------------------------------------------------------ #
    # Controles de ciclo de vida                                           #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """Inicia o cenário: transição IDLE→PLAYING."""
        self._phase = DemoPhase.PLAYING
        self._phase_entry_wall = time.monotonic()
        self._last_tick_wall = time.monotonic()
        self._player.reset()
        self._logs.clear()
        self._metrics.clear()
        self._spans.clear()
        self.last_postmortem = None
        self._warning_wall_time = None
        self._lead_time_seconds = None
        self._phase_transitions = [_PhaseTransition(
            from_phase="IDLE", to_phase="PLAYING",
            wall_time=time.monotonic(),
            sim_time=self._player.sim_start.isoformat(),
        )]

        # Inicia motor tradicional se em modo comparação
        if self._comparison_active and self._trad_player is not None:
            self._trad_player.reset()
            self._trad_logs.clear()
            self._trad_metrics.clear()
            self._trad_spans.clear()
            self._trad_phase = DemoPhase.PLAYING
            self._trad_warning_wall_time = None

    def stop(self) -> None:
        """Para o cenário: volta para IDLE."""
        self._phase = DemoPhase.IDLE

    def enable_comparison(self, corpus: list[CorpusItem]) -> None:
        """Ativa modo de comparação com um segundo motor (enable_prediction=False)."""
        self._comparison_active = True
        self._trad_player = ScenarioPlayer(corpus, speed_factor=self._player.speed_factor)
        self._trad_analyzer = DemoAnalyzer(enable_prediction=False)
        self._trad_logs.clear()
        self._trad_metrics.clear()
        self._trad_spans.clear()
        self._trad_warning_wall_time = None
        # Se o motor principal já está rodando, inicia o motor tradicional também
        if self._phase != DemoPhase.IDLE:
            self._trad_phase = DemoPhase.PLAYING
        else:
            self._trad_phase = DemoPhase.IDLE

    def disable_comparison(self) -> None:
        """Desativa modo de comparação."""
        self._comparison_active = False
        self._trad_player = None
        self._trad_analyzer = None

    def update_speed(self, speed_factor: float) -> None:
        """Atualiza speed_factor em tempo real sem reiniciar o cenário."""
        self._player.update_speed(speed_factor)
        if self._trad_player is not None:
            self._trad_player.update_speed(speed_factor)

    # ------------------------------------------------------------------ #
    # Geração de tick                                                      #
    # ------------------------------------------------------------------ #

    def tick(self) -> TickFrame | dict:
        """Avança um tick e retorna TickFrame (ou dict com left/right em modo comparação)."""
        now = time.monotonic()
        elapsed = now - self._last_tick_wall
        self._last_tick_wall = now

        # Avança player principal
        new_items = self._player.advance(elapsed)
        self._ingest_items(new_items, self._logs, self._metrics, self._spans)

        # Análise preditiva
        result = self._analyzer.analyze(self._logs, self._metrics, self._spans)
        self._advance_phase(result["risk_score"], now)

        # Verifica se fase chegou a RESOLVED para gerar postmortem
        if self._phase == DemoPhase.RESOLVED and self.last_postmortem is None:
            self.last_postmortem = self._build_postmortem(result)

        # Calcula lead time (apenas em modo comparação)
        lead_time: float | None = None
        if self._comparison_active and self._warning_wall_time is not None:
            if self._trad_warning_wall_time is None:
                lead_time = now - self._warning_wall_time
            else:
                lead_time = self._trad_warning_wall_time - self._warning_wall_time

        frame_left = self._build_frame(result, self._phase, lead_time, self._logs)

        if not self._comparison_active:
            return frame_left

        # Motor tradicional
        trad_items = self._trad_player.advance(elapsed) if self._trad_player else []
        self._ingest_items(trad_items, self._trad_logs, self._trad_metrics, self._trad_spans)

        trad_result = self._trad_analyzer.analyze(
            self._trad_logs, self._trad_metrics, self._trad_spans
        ) if self._trad_analyzer else {"risk_score": 0.0, "signals": {}, "prediction": None, "metrics": {}}

        self._advance_trad_phase(trad_result["risk_score"], now)

        frame_right = self._build_frame(trad_result, self._trad_phase, None, self._trad_logs)

        return {
            "left": frame_left.dict(),
            "right": frame_right.dict(),
            "lead_time_seconds": lead_time,
            "comparison": True,
        }

    # ------------------------------------------------------------------ #
    # Helpers internos                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _ingest_items(
        items: list[CorpusItem],
        logs: list[LogEntry],
        metrics: list[MetricPoint],
        spans: list[Span],
    ) -> None:
        """Separa itens do corpus nos buffers tipados correspondentes."""
        for _ts, item in items:
            if isinstance(item, LogEntry):
                logs.append(item)
            elif isinstance(item, MetricPoint):
                metrics.append(item)
            elif isinstance(item, Span):
                spans.append(item)

    def _advance_phase(self, risk_score: float, wall_now: float) -> None:
        """Avança a máquina de estado principal com base no score de risco."""
        old_phase = self._phase

        if self._phase == DemoPhase.PLAYING:
            if risk_score >= 30.0:
                self._phase = DemoPhase.DEGRADING
        elif self._phase == DemoPhase.DEGRADING:
            if risk_score >= self.config.risk_warning_threshold:
                self._phase = DemoPhase.WARNING
                self._warning_wall_time = wall_now
        elif self._phase == DemoPhase.WARNING:
            if risk_score >= self.config.risk_alert_threshold:
                self._phase = DemoPhase.ALERT
                self._phase_entry_wall = wall_now
        elif self._phase == DemoPhase.ALERT:
            # Sustenta ALERT por 30s simulados antes de INCIDENT
            if (wall_now - self._phase_entry_wall) * self._player.speed_factor > 30.0:
                self._phase = DemoPhase.INCIDENT
                self._phase_entry_wall = wall_now
        elif self._phase == DemoPhase.INCIDENT:
            time_elapsed = (wall_now - self._phase_entry_wall) * self._player.speed_factor
            # Recovery real: score caiu abaixo de 15 após pelo menos 30s simulados
            # Fallback: 900s simulados (15 min) — tempo suficiente para corpus de recovery
            # atingir o minuto 25 (fix) e decair até RESOLVED naturalmente.
            # Para cenários sem fix (payment-pool-exhaustion), o corpus exaure e o score
            # não cai, disparando o fallback após ~15 min simulados de incidente.
            if (risk_score < 15.0 and time_elapsed > 30.0) or time_elapsed > 1800.0:
                self._phase = DemoPhase.RESOLVED

        if old_phase != self._phase:
            self._phase_transitions.append(_PhaseTransition(
                from_phase=old_phase.value,
                to_phase=self._phase.value,
                wall_time=wall_now,
                sim_time=self._player.sim_now.isoformat(),
            ))

    def _advance_trad_phase(self, risk_score: float, wall_now: float) -> None:
        """Avança a máquina de estado do motor tradicional."""
        old_phase = self._trad_phase

        if self._trad_phase == DemoPhase.PLAYING:
            if risk_score >= 30.0:
                self._trad_phase = DemoPhase.DEGRADING
        elif self._trad_phase == DemoPhase.DEGRADING:
            # Modo tradicional usa limiar mais alto (3-sigma → ~75)
            if risk_score >= 75.0:
                self._trad_phase = DemoPhase.WARNING
                self._trad_warning_wall_time = wall_now
        elif self._trad_phase == DemoPhase.WARNING:
            if risk_score >= 90.0:
                self._trad_phase = DemoPhase.ALERT

    def _build_frame(
        self,
        result: dict,
        phase: DemoPhase,
        lead_time: float | None,
        log_buffer: list[LogEntry] | None = None,
    ) -> TickFrame:
        """Constrói um TickFrame a partir do resultado da análise."""
        logs_src = log_buffer if log_buffer is not None else self._logs
        recent_logs = [
            {
                "timestamp": lg.timestamp.isoformat(),
                "level": lg.level,
                "service": lg.service,
                "message": lg.message,
            }
            for lg in logs_src[-20:]
        ]

        return TickFrame(
            phase=phase.value,
            elapsed_sim_seconds=self._player.elapsed_sim_seconds,
            sim_time=self._player.sim_now.isoformat(),
            risk_score=result["risk_score"],
            signals=result["signals"],
            prediction=result["prediction"],
            recent_logs=recent_logs,
            metrics=result["metrics"],
            scenario_name=self.scenario_name,
            lead_time_seconds=lead_time,
        )

    def _build_postmortem(self, last_result: dict) -> str:
        """Gera documento markdown de postmortem quando fase chega a RESOLVED."""
        transitions_text = "\n".join(
            f"- {t.from_phase} → {t.to_phase} @ {t.sim_time}"
            for t in self._phase_transitions
        )

        lead_time_text = "N/A"
        if self._lead_time_seconds is not None:
            lt = self._lead_time_seconds
            lt_min = int(lt // 60)
            lt_sec = int(lt % 60)
            lead_time_text = f"{lt}s ({lt_min}m {lt_sec}s)"
        elif self._warning_wall_time is not None:
            elapsed = time.monotonic() - self._warning_wall_time
            lt_min = int(elapsed // 60)
            lt_sec = int(elapsed % 60)
            lead_time_text = f"{elapsed:.1f}s ({lt_min}m {lt_sec}s)"

        signals_fired = [k for k, v in last_result.get("signals", {}).items()
                         if v and k != "novel_templates"]

        root_cause_map = {
            "payment-pool-exhaustion": (
                "Config change reduziu max_connections de 200 para 50, "
                "causando connection pool exhaustion sob carga normal."
            ),
            "payment-pool-recovery": (
                "Surge de tráfego saturou o connection pool (max_connections=200). "
                "Operador aumentou max_connections para 500 no minuto 25, "
                "despressurizando o pool e normalizando latência e logs."
            ),
            "memory-leak": (
                "Memory leak em heap Java — objeto retido por referência estática "
                "acumulou 7987MB ao longo de 60 minutos até OOM."
            ),
            "unknown-fault": (
                "Falha de hardware (IRQ storm) causou comportamento não documentado "
                "no kernel scheduler, gerando timeouts em cascata."
            ),
        }
        root_cause = root_cause_map.get(self.scenario_name, "Causa raiz não mapeada.")

        return f"""# Incident Postmortem — {self.scenario_name}

**Date:** {self._player.sim_start.strftime("%Y-%m-%d")}
**Scenario:** {self.scenario_name}

## Phase Timeline

{transitions_text}

## Predictive Lead Time

**{lead_time_text}**

## Signals Fired

{', '.join(signals_fired) if signals_fired else 'none'}

## Root Cause

{root_cause}

---
*Generated by AIOps Agent Demo — predictive detection.*
"""

    # ------------------------------------------------------------------ #
    # Propriedades públicas                                                #
    # ------------------------------------------------------------------ #

    @property
    def phase(self) -> DemoPhase:
        return self._phase

    @property
    def is_running(self) -> bool:
        return self._phase not in (DemoPhase.IDLE, DemoPhase.RESOLVED)

    @property
    def comparison_active(self) -> bool:
        return self._comparison_active
