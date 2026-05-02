from __future__ import annotations

"""
Camada de narrativa do dashboard de demonstração.

Mapeia (cenário, fase) -> conteúdo PT-BR (timeline, business impact,
hero/lead-time, mock de Slack) consumido pela rota /demo/story.

A fase continua sendo a única fonte de verdade — esta camada apenas
faz lookup local fase → texto/número, sem alterar o cálculo de
risk_score, thresholds ou a cascata scenarios.py → analyzer.py →
engine._advance_phase → _maybe_notify_slack.

Especificação completa em:
    docs/superpowers/specs/2026-05-02-demo-storytelling-design.md
"""

from dataclasses import dataclass
from typing import Mapping


# ------------------------------------------------------------------ #
# Dataclasses                                                         #
# ------------------------------------------------------------------ #


@dataclass(frozen=True)
class SlackMockMessage:
    """Conteúdo visual de um card no painel de mock do Slack."""

    channel: str           # ex: "#payments-incidents"
    author: str            # ex: "AIOps Bot"
    title: str             # ex: "[WARNING] Predição de incidente"
    body: str              # texto principal (markdown leve)
    severity: str          # "warning" | "critical" | "info" | "success"
    timestamp_label: str   # ex: "+18 min" (sim time)


@dataclass(frozen=True)
class PhaseNarrative:
    """Conteúdo narrativo de uma fase específica."""

    timeline_label: str
    business_impact_per_min: int
    impact_color: str                       # "green" | "amber" | "red"
    hero_text: str | None                   # None = não exibir hero bar
    hero_lead_time_seconds: int | None
    slack_message: SlackMockMessage | None  # None = fase não dispara Slack


@dataclass(frozen=True)
class StoryNarrative:
    """Narrativa completa de um cenário."""

    scenario_name: str
    app_name: str
    app_context: str
    baseline_revenue_per_min: int
    # Chaves: "PLAYING", "DEGRADING", "WARNING", "ALERT", "INCIDENT", "RESOLVED"
    phases: Mapping[str, PhaseNarrative]


# ------------------------------------------------------------------ #
# Conteúdo dos cards de Slack                                         #
# ------------------------------------------------------------------ #
# Body em formato key-value markdown leve (mesmo padrão da WARNING
# literal do spec). Para ALERT/INCIDENT/RESOLVED o body foi composto
# a partir do resumo italicizado da tabela do spec preservando o
# formato Sinal/ETA/Impacto/Ação.

_SLACK_WARNING_BODY = (
    "**Sinal**: rate_ratio 2.5 (burst de erros)\n"
    "**ETA**: ~4 min para incidente crítico\n"
    "**Impacto projetado**: PoolExhausted no serviço de pagamentos\n"
    "**Ação sugerida**: revisar pool ativo (db-pool) ou escalar max_connections"
)

_SLACK_ALERT_BODY = (
    "**Sinal**: rate_ratio 3+ (cascata de falhas)\n"
    "**ETA**: < 1 min para incidente confirmado\n"
    "**Impacto projetado**: circuit breakers abrindo, transações em fila\n"
    "**Ação imediata**: executar runbook RB-012"
)

_SLACK_INCIDENT_BODY = (
    "**Causa raiz**: pool exhausted em db-pool (200 max, 164 ativos)\n"
    "**Fix**: aumentar para 500 + rolling restart\n"
    "**Confiança**: 0.94"
)

_SLACK_RESOLVED_BODY = (
    "**Status**: incidente em payment-service resolvido\n"
    "**Pool ativo**: 18% (era 86%)\n"
    "**MTTR**: 8 min\n"
    "**Observação**: sem falsos positivos no decay"
)


# ------------------------------------------------------------------ #
# Cenário 1 — payment-pool-exhaustion                                 #
# ------------------------------------------------------------------ #
# 32 min, termina em INCIDENT. Fase RESOLVED nunca é alcançada em
# runtime (corpus não permite score < 15 antes do timeout), mas a
# entrada existe para manter `phases` com chaves idênticas em todos
# os cenários — frontend faz lookup direto sem checar ausência.

_EXHAUSTION_PHASES: Mapping[str, PhaseNarrative] = {
    "PLAYING": PhaseNarrative(
        timeline_label="00:00 — Operação normal · transações fluindo · pool a 5–25%",
        business_impact_per_min=0,
        impact_color="green",
        hero_text=None,
        hero_lead_time_seconds=None,
        slack_message=None,
    ),
    "DEGRADING": PhaseNarrative(
        timeline_label="00:08 — Latência sobe · pool em 25–50% · primeiros timeouts esporádicos",
        business_impact_per_min=5_000,
        impact_color="amber",
        hero_text=None,
        hero_lead_time_seconds=None,
        slack_message=None,
    ),
    "WARNING": PhaseNarrative(
        timeline_label="00:18 — Burst de erros · AIOps detecta padrão de falha · alerta predictive disparado",
        business_impact_per_min=50_000,
        impact_color="amber",
        hero_text="⚠ Predição de incidente — 4 min até a falha · Aja agora",
        hero_lead_time_seconds=240,
        slack_message=SlackMockMessage(
            channel="#payments-incidents",
            author="AIOps Bot",
            title="[WARNING] Predição de incidente em payment-service",
            body=_SLACK_WARNING_BODY,
            severity="warning",
            timestamp_label="+18 min",
        ),
    ),
    "ALERT": PhaseNarrative(
        timeline_label="00:22 — Score crítico · pool a 75–86% · circuit breakers abrindo",
        business_impact_per_min=200_000,
        impact_color="red",
        hero_text="🔴 ALERTA CRÍTICO — incidente em <1 min",
        hero_lead_time_seconds=30,
        slack_message=SlackMockMessage(
            channel="#payments-incidents",
            author="AIOps Bot",
            title="[CRITICAL] Incidente iminente em payment-service",
            body=_SLACK_ALERT_BODY,
            severity="critical",
            timestamp_label="+22 min",
        ),
    ),
    "INCIDENT": PhaseNarrative(
        timeline_label="00:22 — INCIDENTE confirmado · diagnóstico completo · runbook recomendado",
        business_impact_per_min=500_000,
        impact_color="red",
        hero_text="🚨 INCIDENTE EM CURSO — diagnóstico disponível",
        hero_lead_time_seconds=None,
        slack_message=SlackMockMessage(
            channel="#payments-incidents",
            author="AIOps Bot",
            title="[DIAGNOSIS] Causa raiz identificada em payment-service",
            body=_SLACK_INCIDENT_BODY,
            severity="critical",
            timestamp_label="+22 min",
        ),
    ),
    # Fase fallback — fase não alcançada neste cenário em runtime.
    "RESOLVED": PhaseNarrative(
        timeline_label="(não alcançado neste cenário)",
        business_impact_per_min=0,
        impact_color="green",
        hero_text=None,
        hero_lead_time_seconds=None,
        slack_message=None,
    ),
}


# ------------------------------------------------------------------ #
# Cenário 2 — payment-pool-recovery                                   #
# ------------------------------------------------------------------ #
# 40 min, dispara as 4 mensagens de Slack. Timings derivam de
# random.seed(44) + curva de 3 atos: WARNING ~min 17, ALERT ~min 20,
# INCIDENT ~min 20.5, RESOLVED ~min 30 após o fix operacional.

_RECOVERY_PHASES: Mapping[str, PhaseNarrative] = {
    "PLAYING": PhaseNarrative(
        timeline_label="00:00 — Operação normal · transações fluindo · pool a 5–25%",
        business_impact_per_min=0,
        impact_color="green",
        hero_text=None,
        hero_lead_time_seconds=None,
        slack_message=None,
    ),
    "DEGRADING": PhaseNarrative(
        timeline_label="00:08 — Latência sobe · pool em 25–50% · primeiros timeouts esporádicos",
        business_impact_per_min=5_000,
        impact_color="amber",
        hero_text=None,
        hero_lead_time_seconds=None,
        slack_message=None,
    ),
    "WARNING": PhaseNarrative(
        timeline_label="00:17 — Burst de erros · AIOps detecta padrão de falha · alerta predictive disparado",
        business_impact_per_min=50_000,
        impact_color="amber",
        hero_text="⚠ Predição de incidente — 3 min até a falha · Aja agora",
        hero_lead_time_seconds=180,
        slack_message=SlackMockMessage(
            channel="#payments-incidents",
            author="AIOps Bot",
            title="[WARNING] Predição de incidente em payment-service",
            body=_SLACK_WARNING_BODY,
            severity="warning",
            timestamp_label="+17 min",
        ),
    ),
    "ALERT": PhaseNarrative(
        timeline_label="00:20 — Score crítico · pool a 75–86% · circuit breakers abrindo",
        business_impact_per_min=200_000,
        impact_color="red",
        hero_text="🔴 ALERTA CRÍTICO — incidente em <1 min",
        hero_lead_time_seconds=30,
        slack_message=SlackMockMessage(
            channel="#payments-incidents",
            author="AIOps Bot",
            title="[CRITICAL] Incidente iminente em payment-service",
            body=_SLACK_ALERT_BODY,
            severity="critical",
            timestamp_label="+20 min",
        ),
    ),
    "INCIDENT": PhaseNarrative(
        timeline_label="00:20 — INCIDENTE · runbook aplicado · max_connections 200→500 em curso",
        business_impact_per_min=500_000,
        impact_color="red",
        hero_text="🚨 INCIDENTE EM CURSO — fix em aplicação",
        hero_lead_time_seconds=None,
        slack_message=SlackMockMessage(
            channel="#payments-incidents",
            author="AIOps Bot",
            title="[DIAGNOSIS] Causa raiz identificada em payment-service",
            body=_SLACK_INCIDENT_BODY,
            severity="critical",
            timestamp_label="+20 min",
        ),
    ),
    "RESOLVED": PhaseNarrative(
        timeline_label="00:30 — Recuperação confirmada · pool a 18% · score < 15 · sistema saudável",
        business_impact_per_min=0,
        impact_color="green",
        hero_text="✅ INCIDENTE RESOLVIDO — recuperação predita confirmada",
        hero_lead_time_seconds=None,
        slack_message=SlackMockMessage(
            channel="#payments-incidents",
            author="AIOps Bot",
            title="[RESOLVED] Incidente em payment-service resolvido",
            body=_SLACK_RESOLVED_BODY,
            severity="success",
            timestamp_label="+30 min",
        ),
    ),
}


# ------------------------------------------------------------------ #
# Registro de narrativas                                              #
# ------------------------------------------------------------------ #
# Header da story (igual em ambos os cenários):
#   PayFlow Payments · Produção · Pico Black Friday · 4,2 mi req/min
#   · R$ 4,2 mi/min em transações

_APP_NAME = "PayFlow Payments"
_APP_CONTEXT = "Produção · Pico Black Friday · 4,2 mi req/min"
_BASELINE_REVENUE_PER_MIN = 4_200_000


NARRATIVES: Mapping[str, StoryNarrative] = {
    "payment-pool-exhaustion": StoryNarrative(
        scenario_name="payment-pool-exhaustion",
        app_name=_APP_NAME,
        app_context=_APP_CONTEXT,
        baseline_revenue_per_min=_BASELINE_REVENUE_PER_MIN,
        phases=_EXHAUSTION_PHASES,
    ),
    "payment-pool-recovery": StoryNarrative(
        scenario_name="payment-pool-recovery",
        app_name=_APP_NAME,
        app_context=_APP_CONTEXT,
        baseline_revenue_per_min=_BASELINE_REVENUE_PER_MIN,
        phases=_RECOVERY_PHASES,
    ),
}


def get_narrative(scenario: str) -> StoryNarrative | None:
    """Retorna a narrativa do cenário, ou None se desconhecido."""
    return NARRATIVES.get(scenario)
