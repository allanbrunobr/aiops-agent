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

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable

from domain.incident import MitigationAction


class TNRResult(Enum):
    SUCCESS = "success"
    REGRESSION = "regression"
    ROLLBACK_COMPLETE = "rollback_complete"
    ERROR = "error"
    # Sem medição de saúde pós-ação (a ação não foi executada): não é sucesso
    # nem regressão. Antes o protótipo inventava uma melhora e dava SUCCESS.
    NOT_VERIFIED = "not_verified"


@dataclass
class HealthSnapshot:
    """Snapshot do estado de saúde do sistema."""

    timestamp: datetime
    error_rate: float
    latency_p99_ms: float
    availability: float
    connection_pool_active: int
    connection_pool_max: int

    def validation_error(self) -> str | None:
        """Motivo pelo qual o snapshot não é confiável, ou None se válido.

        Não-finito (NaN/inf), error_rate<0, availability fora de [0, 100],
        latência<0 ou pool negativo → não-confiável (B3, fail-closed).
        """
        floats = {
            "error_rate": self.error_rate,
            "latency_p99_ms": self.latency_p99_ms,
            "availability": self.availability,
        }
        for name, value in floats.items():
            if not math.isfinite(value):
                return f"{name} não-finito ({value})"
        if self.error_rate < 0:
            return f"error_rate negativo ({self.error_rate})"
        if self.latency_p99_ms < 0:
            return f"latency_p99_ms negativo ({self.latency_p99_ms})"
        if not 0 <= self.availability <= 100:
            return f"availability fora de [0, 100] ({self.availability})"
        if self.connection_pool_active < 0 or self.connection_pool_max < 0:
            return "connection pool negativo"
        return None

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
    """Relatório do TNR check.

    verification_mode distingue simulação de validação real: o protótipo
    NUNCA executa ações em infra real (SECURITY.md), então checks simulados
    são marcados como "simulated" e devem ser apresentados como tal no
    postmortem/audit (F2-3).
    """

    result: TNRResult
    pre_snapshot: HealthSnapshot | None
    post_snapshot: HealthSnapshot | None
    action: MitigationAction
    health_delta: float
    message: str
    verification_mode: str = "simulated"


def simulate_action_effect(
    action: MitigationAction, pre: HealthSnapshot
) -> HealthSnapshot:
    """Projeção determinística do efeito de uma ação — SIMULAÇÃO DECLARADA.

    Como nenhuma ação é executada em infra real (SECURITY.md), o snapshot
    pós-ação é uma projeção, não uma medição:
    - comandos destrutivos (delete/rm/drop/truncate/replicas=0) → degradação;
    - rollback/undo/restart/scale-up → recuperação em direção ao baseline;
    - demais → estado inalterado (delta 0, dentro da tolerância).

    A projeção é conservadora e documentada; não substitui validação real.
    """
    cmd = (action.command or "").lower()
    destructive = any(
        p in cmd for p in ("delete", "rm -", "drop ", "truncate", "scale down")
    ) or bool(re.search(r"--replicas[=\s]+0+\b", cmd))
    recovery = not destructive and (
        any(p in cmd for p in ("rollout undo", "rollback", "restart"))
        or bool(re.search(r"--replicas[=\s]", cmd))
    )
    now = datetime.now()
    if destructive:
        return HealthSnapshot(
            timestamp=now,
            error_rate=min(100.0, pre.error_rate * 1.5 + 10.0),
            latency_p99_ms=pre.latency_p99_ms * 1.5,
            availability=max(0.0, pre.availability - 20.0),
            connection_pool_active=pre.connection_pool_active,
            connection_pool_max=pre.connection_pool_max,
        )
    if recovery:
        error_rate = max(0.1, pre.error_rate * 0.05)
        return HealthSnapshot(
            timestamp=now,
            error_rate=error_rate,
            latency_p99_ms=max(120.0, pre.latency_p99_ms * 0.1),
            availability=min(99.9, 100.0 - error_rate),
            connection_pool_active=max(0, pre.connection_pool_active // 2),
            connection_pool_max=max(
                pre.connection_pool_max, pre.connection_pool_active * 4
            ),
        )
    return HealthSnapshot(
        timestamp=now,
        error_rate=pre.error_rate,
        latency_p99_ms=pre.latency_p99_ms,
        availability=pre.availability,
        connection_pool_active=pre.connection_pool_active,
        connection_pool_max=pre.connection_pool_max,
    )


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

    def _untrusted_report(
        self,
        action: MitigationAction,
        pre: HealthSnapshot,
        post: HealthSnapshot,
        mode: str,
    ) -> TNRReport | None:
        """ERROR (nunca SUCCESS) se algum snapshot ou o delta for não-confiável."""
        reason = pre.validation_error()
        if reason:
            reason = f"pre-snapshot: {reason}"
        else:
            reason = post.validation_error()
            if reason:
                reason = f"post-snapshot: {reason}"
        delta = 0.0
        if reason is None:
            delta = post.health_score - pre.health_score
            if not math.isfinite(delta):
                reason = f"health_delta não-finito ({delta})"
        if reason is None:
            return None
        logging.getLogger(__name__).warning(
            "TNR: medição não-confiável → ERROR (%s)", reason
        )
        report = TNRReport(
            result=TNRResult.ERROR,
            pre_snapshot=pre,
            post_snapshot=post,
            action=action,
            health_delta=0.0,
            message=f"ERROR: medição de health não-confiável ({reason})",
            verification_mode=mode,
        )
        self.history.append(report)
        return report

    def execute_with_safety(
        self,
        action: MitigationAction,
        pre_state: HealthSnapshot,
        execute_fn: Callable[[MitigationAction], object],
        post_state_fn: Callable[[], HealthSnapshot],
        rollback_fn: Callable[[MitigationAction], object] | None = None,
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
                verification_mode="real",
            )
            self.history.append(report)
            return report

        # 3. Captura estado PÓS
        try:
            post_snapshot = post_state_fn()
        except Exception as e:
            # B6: sem estado pós não há como provar não-regressão → fail-closed.
            logging.getLogger(__name__).warning(
                "TNR: post_state_fn falhou → ERROR (%s)", e
            )
            report = TNRReport(
                result=TNRResult.ERROR,
                pre_snapshot=pre_snapshot,
                post_snapshot=pre_snapshot,
                action=action,
                health_delta=0,
                message=(
                    f"ERROR: post_state_fn failed ({e}); "
                    "não foi possível verificar não-regressão"
                ),
                verification_mode="real",
            )
            self.history.append(report)
            return report

        # 3b. Guarda B3: snapshot inválido / delta não-finito → ERROR
        untrusted = self._untrusted_report(action, pre_snapshot, post_snapshot, "real")
        if untrusted is not None:
            return untrusted

        # 4. Compara
        health_delta = post_snapshot.health_score - pre_snapshot.health_score

        if health_delta < self.regression_threshold:
            # REGRESSÃO DETECTADA — rollback
            if rollback_fn:
                try:
                    rollback_fn(action)
                except Exception as e:
                    # B6: rollback que falha NUNCA é ROLLBACK_COMPLETE.
                    logging.getLogger(__name__).warning(
                        "TNR: rollback_fn falhou → ERROR (%s)", e
                    )
                    report = TNRReport(
                        result=TNRResult.ERROR,
                        pre_snapshot=pre_snapshot,
                        post_snapshot=post_snapshot,
                        action=action,
                        health_delta=health_delta,
                        message=(
                            f"ERROR: regression ({health_delta:.1f} points) and "
                            f"rollback FAILED ({e}). Manual rollback needed."
                        ),
                        verification_mode="real",
                    )
                    self.history.append(report)
                    return report
            report = TNRReport(
                result=TNRResult.ROLLBACK_COMPLETE
                if rollback_fn
                else TNRResult.REGRESSION,
                pre_snapshot=pre_snapshot,
                post_snapshot=post_snapshot,
                action=action,
                health_delta=health_delta,
                message=(
                    f"REGRESSION DETECTED: health dropped {health_delta:.1f} points "
                    f"({pre_snapshot.health_score:.0f}% → {post_snapshot.health_score:.0f}%). "
                    f"{'Rollback executed.' if rollback_fn else 'Manual rollback needed.'}"
                ),
                verification_mode="real",
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
                verification_mode="real",
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
        untrusted = self._untrusted_report(action, pre_health, post_health, "simulated")
        if untrusted is not None:
            return untrusted
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
            verification_mode="simulated",
        )
        self.history.append(report)

        # Emite evento de auditoria (Story 3.4)
        try:
            from safety.audit import SafetyAuditEvent, SafetyAuditor

            is_rollback = result == TNRResult.REGRESSION
            SafetyAuditor.log_decision(
                SafetyAuditEvent(
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
                        "Rollback triggered"
                        if is_rollback
                        else "No regression detected"
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
                        "verification_mode": "simulated",
                    },
                )
            )
        except Exception as exc:
            try:
                from safety.audit import SafetyAuditor

                SafetyAuditor.log_error("tnr", correlation_id, exc)
            except Exception:  # noqa: BLE001 — auditoria nunca derruba a validação
                logging.getLogger(__name__).warning(
                    "audit log_error failed for layer %s",
                    "tnr",
                    exc_info=True,
                )

        return report
