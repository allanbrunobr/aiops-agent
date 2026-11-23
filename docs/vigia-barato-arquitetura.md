# Vigia Barato — arquitetura em duas camadas (VB2 + VB3)

```
MetricsSource (Prometheus/CSV)
        │  consulta janela (somente leitura)
        ▼
┌──────────────────────────────┐   sem LLM, sem token, sem infra
│ Camada 1 — ContinuousMonitor │   EWMA + risco + saturação + portões D3
│ pipeline/continuous_monitor  │   tick determinístico a cada N s
└──────────────┬───────────────┘
               │ should_escalate (nível ≥ warning e ETA/saturação)
               ▼
┌──────────────────────────────┐   EscalationHook
│ Escalada (VB3)               │   1) aviso proativo (Slack)
│                              │   2) IncidentState + NormalizedAlert
└──────────────┬───────────────┘   3) diagnóstico profundo
               ▼
┌──────────────────────────────┐   V1 BlueprintEngine.run (app/monitor_setup.py)
│ Camada 2 — diagnóstico       │   só aqui há LLM/RAG (custo de token)
└──────────────┬───────────────┘
               ▼
   PROPOSTA + postmortem  ──►  aprovação humana (exit 3 / fail-closed)
```

## Economia de tokens
- A camada 1 roda o tempo todo e **não importa `adapters.llm`** (testado): custo
  de token zero, só matemática sobre a janela de métricas.
- A camada 2 (LLM) roda **só sob escalada**, e **uma vez por incidente aberto**:
  o hook faz dedup e só re-arma quando o nível volta ao normal (`reset`) ou
  após `rearm_minutes` (30) ainda escalando. Série plana/ruído nunca escala.

## Aviso proativo
`prediction.warning`/`prediction.alert` com "Incident likely in ~X min" ou
"Saturação no teto", via `SlackNotifier`. **Dry-run por padrão**; envio real
só com `AIOPS_MONITOR_SLACK_REAL=1` **e** `SLACK_BOT_TOKEN`
(`build_monitor_slack_notifier`). Falha de Slack nunca bloqueia o diagnóstico.

## Garantias (fail-closed)
- O monitor **não substitui a aprovação humana**: o resultado é proposta +
  postmortem. `EscalationResult.executed_infra` é sempre `False` e
  `requires_human_approval` sempre `True`; nenhum caminho do monitor executa
  ação em infra.
- A previsão é registrada no audit com `verification_mode: "simulated"` (é uma
  projeção estatística, não medição pós-ação) — não deve ser apresentada como
  validação real.
- Falha do diagnóstico ou da notificação é capturada: o vigia continua.
- `atick()` roda o tick (incluindo o diagnóstico síncrono) via
  `asyncio.to_thread`, sem bloquear o event loop.

## Uso
```python
from app.monitor_setup import make_blueprint_diagnose
from interfaces.http.notifications.slack import build_monitor_slack_notifier
from pipeline.continuous_monitor import ContinuousMonitor, EscalationHook

hook = EscalationHook(
    make_blueprint_diagnose(app_config),
    notify=build_monitor_slack_notifier().on_event,  # dry-run por padrão
    service="payment-svc",
)
monitor = ContinuousMonitor(source, escalation=hook)
```
(`notify` aceita callback síncrono ou async.)

Limite: o lead time depende de `ewma_threshold` calibrado por métrica (ver
`docs/preditivo-backtest.md`). O backtest do monitor é a tarefa VB4.
