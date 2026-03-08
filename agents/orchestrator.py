"""
Orchestrator — State Machine para Incident Response.

Baseado no paper STRATUS (NeurIPS 2025, UIUC + IBM Research):
  - Estados: IDLE → DETECTING → DIAGNOSING → MITIGATING → VERIFYING → RESOLVED
  - Transições controladas por resultado de cada agente
  - Se TNR detecta regressão → ROLLBACK → DIAGNOSING

Ref: "STRATUS consists of multiple specialized agents organized in a state machine
to assist system-level safety reasoning and enforcement."
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()


class State(Enum):
    IDLE = "IDLE"
    DETECTING = "DETECTING"
    DIAGNOSING = "DIAGNOSING"
    MITIGATING = "MITIGATING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    ROLLBACK = "ROLLBACK"
    FAILED = "FAILED"


VALID_TRANSITIONS = {
    State.IDLE: {State.DETECTING},
    State.DETECTING: {State.DIAGNOSING, State.IDLE},
    State.DIAGNOSING: {State.MITIGATING, State.FAILED},
    State.MITIGATING: {State.VERIFYING},
    State.VERIFYING: {State.RESOLVED, State.ROLLBACK},
    State.ROLLBACK: {State.DIAGNOSING},
    State.RESOLVED: {State.IDLE},
    State.FAILED: {State.IDLE},
}

STATE_ICONS = {
    State.IDLE: "💤",
    State.DETECTING: "🔔",
    State.DIAGNOSING: "🔍",
    State.MITIGATING: "🛠️",
    State.VERIFYING: "✅",
    State.RESOLVED: "🎉",
    State.ROLLBACK: "⏪",
    State.FAILED: "❌",
}


@dataclass
class StateTransition:
    from_state: State
    to_state: State
    timestamp: datetime
    reason: str


class Orchestrator:
    """State machine que coordena o incident response."""

    def __init__(self):
        self.current_state = State.IDLE
        self.history: list[StateTransition] = []
        self.context: dict = {}

    def transition(self, to_state: State, reason: str = ""):
        """Transiciona para um novo estado com validação."""
        if to_state not in VALID_TRANSITIONS.get(self.current_state, set()):
            raise InvalidTransitionError(
                f"Cannot transition from {self.current_state.value} to {to_state.value}. "
                f"Valid: {[s.value for s in VALID_TRANSITIONS[self.current_state]]}"
            )

        transition = StateTransition(
            from_state=self.current_state,
            to_state=to_state,
            timestamp=datetime.now(),
            reason=reason,
        )
        self.history.append(transition)

        icon = STATE_ICONS.get(to_state, "➡️")
        console.print(f"\n{icon} [{to_state.value}] {reason}", style="bold")

        self.current_state = to_state

    def run_incident_response(
        self,
        detector,
        diagnoser,
        mitigator,
        tnr_guard,
        report,
        retriever,
    ) -> dict:
        """Executa o ciclo completo de incident response."""
        result = {"success": False, "stages": {}}

        # 1. DETECTING
        self.transition(State.DETECTING, "Analisando dados de observabilidade...")
        detection = detector.detect(report)
        result["stages"]["detection"] = detection

        if not detection.get("is_incident"):
            self.transition(State.IDLE, "Nenhum incidente detectado")
            return result

        # 2. DIAGNOSING
        self.transition(State.DIAGNOSING, f"Investigando incidente em {', '.join(detection.get('affected_services', []))}")
        diagnosis = diagnoser.diagnose(report, retriever)
        result["stages"]["diagnosis"] = diagnosis

        if not diagnosis.get("root_cause"):
            self.transition(State.FAILED, "Não foi possível identificar root cause")
            return result

        # 3. MITIGATING
        self.transition(State.MITIGATING, "Propondo remediação...")
        mitigation = mitigator.propose(diagnosis)
        result["stages"]["mitigation"] = mitigation

        # 4. VERIFYING (TNR)
        self.transition(State.VERIFYING, "Verificando impacto (TNR)...")
        tnr_result = mitigator.verify_with_tnr(mitigation, tnr_guard)
        result["stages"]["verification"] = {
            "result": tnr_result.result.value,
            "health_delta": tnr_result.health_delta,
            "message": tnr_result.message,
        }

        if tnr_result.result.value == "regression":
            self.transition(State.ROLLBACK, f"Regressão detectada: {tnr_result.message}")
            result["stages"]["rollback"] = {"message": "Rollback executado, retornando ao diagnóstico"}
            # Em produção, voltaria ao DIAGNOSING para tentar outra abordagem
        else:
            self.transition(State.RESOLVED, f"Incidente resolvido: {tnr_result.message}")
            result["success"] = True

        # Gerar timeline
        result["timeline"] = self._generate_timeline()
        return result

    def _generate_timeline(self) -> list[dict]:
        """Gera timeline do incidente."""
        return [
            {
                "time": t.timestamp.strftime("%H:%M:%S"),
                "from": t.from_state.value,
                "to": t.to_state.value,
                "reason": t.reason,
            }
            for t in self.history
        ]


class InvalidTransitionError(Exception):
    pass
