"""
Transactional No-Regression (TNR) Safety Mechanism.

Baseado no paper STRATUS (NeurIPS 2025, UIUC + IBM Research):
  - Captura snapshot de health ANTES da ação
  - Executa a ação de mitigação
  - Captura snapshot DEPOIS
  - Se piorou → rollback automático
  - Se melhorou → confirma a mitigação

Garante que o agente NUNCA piore o sistema.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from domain.incident import MitigationAction


class TNRResult(Enum):
    SUCCESS = "success"
    REGRESSION = "regression"
    ROLLBACK_COMPLETE = "rollback_complete"
    ERROR = "error"


@dataclass
class HealthSnapshot:
    """Snapshot do estado de saúde do sistema."""
    timestamp: datetime
    error_rate: float
    latency_p99_ms: float
    availability: float
    connection_pool_active: int
    connection_pool_max: int

    @property
    def health_score(self) -> float:
        """Score de 0-100 baseado nas métricas."""
        scores = []

        # Error rate (0% = 100pts, >50% = 0pts)
        scores.append(max(0, 100 - self.error_rate * 2))

        # Latency (< 200ms = 100pts, > 5000ms = 0pts)
        scores.append(max(0, min(100, 100 - (self.latency_p99_ms - 200) / 48)))

        # Availability (100% = 100pts, linear down)
        scores.append(self.availability)

        # Connection pool (< 80% used = 100pts)
        if self.connection_pool_max > 0:
            pool_usage = self.connection_pool_active / self.connection_pool_max
            scores.append(max(0, 100 - pool_usage * 100))
        else:
            scores.append(50)

        return round(sum(scores) / len(scores), 1)

    def __str__(self):
        return (
            f"Health={self.health_score:.0f}% "
            f"(err={self.error_rate:.1f}%, lat={self.latency_p99_ms:.0f}ms, "
            f"avail={self.availability:.0f}%, pool={self.connection_pool_active}/{self.connection_pool_max})"
        )



@dataclass
class TNRReport:
    """Relatório do TNR check."""
    result: TNRResult
    pre_snapshot: HealthSnapshot
    post_snapshot: HealthSnapshot
    action: MitigationAction
    health_delta: float
    message: str


class TNRSafetyGuard:
    """Implementação do Transactional No-Regression."""

    def __init__(self, regression_threshold: float = -5.0):
        """
        Args:
            regression_threshold: Delta mínimo de health aceitável.
                                  -5.0 significa que até 5 pontos de queda é tolerado.
        """
        self.regression_threshold = regression_threshold
        self.history: list[TNRReport] = []

    def execute_with_safety(
        self,
        action: MitigationAction,
        pre_state: HealthSnapshot,
        execute_fn: callable,
        post_state_fn: callable,
        rollback_fn: callable | None = None,
    ) -> TNRReport:
        """
        Executa ação com verificação TNR.

        Args:
            action: Ação a executar
            pre_state: Snapshot antes da execução
            execute_fn: Função que executa a ação
            post_state_fn: Função que captura estado pós-execução
            rollback_fn: Função de rollback (opcional)
        """
        # 1. Captura estado PRÉ
        pre_snapshot = pre_state

        # 2. Executa a ação
        try:
            execute_fn(action)
        except Exception as e:
            report = TNRReport(
                result=TNRResult.ERROR,
                pre_snapshot=pre_snapshot,
                post_snapshot=pre_snapshot,
                action=action,
                health_delta=0,
                message=f"Execution failed: {e}",
            )
            self.history.append(report)
            return report

        # 3. Captura estado PÓS
        post_snapshot = post_state_fn()

        # 4. Compara
        health_delta = post_snapshot.health_score - pre_snapshot.health_score

        if health_delta < self.regression_threshold:
            # REGRESSÃO DETECTADA — rollback
            if rollback_fn:
                rollback_fn(action)
            report = TNRReport(
                result=TNRResult.ROLLBACK_COMPLETE if rollback_fn else TNRResult.REGRESSION,
                pre_snapshot=pre_snapshot,
                post_snapshot=post_snapshot,
                action=action,
                health_delta=health_delta,
                message=(
                    f"REGRESSION DETECTED: health dropped {health_delta:.1f} points "
                    f"({pre_snapshot.health_score:.0f}% → {post_snapshot.health_score:.0f}%). "
                    f"{'Rollback executed.' if rollback_fn else 'Manual rollback needed.'}"
                ),
            )
        else:
            # SUCESSO
            report = TNRReport(
                result=TNRResult.SUCCESS,
                pre_snapshot=pre_snapshot,
                post_snapshot=post_snapshot,
                action=action,
                health_delta=health_delta,
                message=(
                    f"Mitigation successful: health improved {health_delta:.1f} points "
                    f"({pre_snapshot.health_score:.0f}% → {post_snapshot.health_score:.0f}%)"
                ),
            )

        self.history.append(report)
        return report

    def simulate_check(
        self,
        action: MitigationAction,
        pre_health: HealthSnapshot,
        post_health: HealthSnapshot,
        *,
        correlation_id: str = "",
    ) -> TNRReport:
        """Simula TNR check sem executar (para demo/testes)."""
        health_delta = post_health.health_score - pre_health.health_score

        if health_delta < self.regression_threshold:
            result = TNRResult.REGRESSION
            message = (
                f"REGRESSION: health {pre_health.health_score:.0f}% → {post_health.health_score:.0f}% "
                f"(delta: {health_delta:.1f})"
            )
        else:
            result = TNRResult.SUCCESS
            message = (
                f"SUCCESS: health {pre_health.health_score:.0f}% → {post_health.health_score:.0f}% "
                f"(delta: +{health_delta:.1f})"
            )

        report = TNRReport(
            result=result,
            pre_snapshot=pre_health,
            post_snapshot=post_health,
            action=action,
            health_delta=health_delta,
            message=message,
        )
        self.history.append(report)

        # Emite evento de auditoria (Story 3.4)
        try:
            from safety.audit import SafetyAuditEvent, SafetyAuditor

            is_rollback = result == TNRResult.REGRESSION
            SafetyAuditor.log_decision(SafetyAuditEvent(
                layer_name="tnr",
                correlation_id=correlation_id,
                verdict="rollback" if is_rollback else "pass",
                rationale=(
                    f"Health regression detected: delta={health_delta:.1f} < "
                    f"threshold={self.regression_threshold}"
                    if is_rollback
                    else f"Health stable: delta={health_delta:.1f} >= "
                         f"threshold={self.regression_threshold}"
                ),
                input_summary=(
                    f"Pre: {pre_health.health_score:.1f}, "
                    f"Post: {post_health.health_score:.1f}"
                ),
                output_summary=(
                    "Rollback triggered" if is_rollback else "No regression detected"
                ),
                triggered_rule=(
                    f"regression_threshold={self.regression_threshold}"
                    if is_rollback
                    else None
                ),
                metadata={
                    "pre_score": pre_health.health_score,
                    "post_score": post_health.health_score,
                    "delta": health_delta,
                },
            ))
        except Exception as exc:
            try:
                from safety.audit import SafetyAuditor
                SafetyAuditor.log_error("tnr", correlation_id, exc)
            except Exception:
                pass

        return report
