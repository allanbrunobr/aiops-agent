# Monitor contínuo (Vigia Barato) — guia

> **Escopo honesto:** prova de conceito. Dados simulados (CSV do cenário HPA) ou um
> Prometheus que você aponta. Fail-closed. O monitor **nunca executa infraestrutura**:
> só propõe (TNR `verification_mode="simulated"`) e toda ação exige aprovação humana.
> Isto **não** é um serviço validado para produção 24/7.

## Arquitetura em duas camadas

1. **Barata (zero token):** `pipeline/continuous_monitor.py` (`ContinuousMonitor`) consulta
   a fonte a cada tick, calcula EWMA/tendência/ETA até saturação e decide `should_escalate`.
   Nenhuma chamada a LLM.
2. **Profunda (sob escalada):** `EscalationHook` roda o diagnóstico V1
   (`app/monitor_setup.py: make_blueprint_diagnose`, `--no-llm` suportado), notifica e
   registra. Reescalada do mesmo serviço respeita um período de rearme (30 min).

Detalhes de design: [`vigia-barato-arquitetura.md`](vigia-barato-arquitetura.md).

## Como rodar

```bash
python main.py --monitor --source csv://<arquivo> --ticks N --no-llm
python main.py --monitor --source prometheus://<host:porta> --interval 60 --no-llm
python scripts/run_continuous_demo.py [--seed 42] [--source csv://<arquivo>] [--workdir <dir>] [--llm]
```

### Flags (`interfaces/cli/args.py`)

| Flag | Significado | Padrão |
|---|---|---|
| `--monitor` | Ativa o modo; exige `--source`; exclusivo com `--scenario`, `--data-source databricks`, `--pipeline-v1` | off |
| `--source URL` | `csv://<caminho>` ou `prometheus://<url>`; outro esquema é erro | — |
| `--interval S` | Segundos entre ticks (> 0). No CSV é o passo do relógio simulado | `60` |
| `--ticks N` | Número de ticks (> 0). Sem ele: CSV roda até o fim; Prometheus roda até Ctrl+C | — |
| `--monitor-service NOME` | Nome do serviço na escalada | `hpa-demo` |
| `--profile auto\|hpa\|generic` | Tuning do monitor (ver abaixo) | `auto` |
| `--no-llm`, `--config` | Reaproveitados do CLI normal (diagnóstico heurístico; limiares) | — |

### Fontes

- **`csv://`** — colunas `timestamp,service,metric_name,value` (formato de
  `data.hpa_saturation_scenario.export_csv`). *Replay*: relógio simulado começando em
  `primeiro_timestamp + 30 min`, sem sleep real. Para métricas do cenário HPA usa a mesma
  calibração do backtest (EWMA 0,9×request, sustentado 0,8×request, ETA 60 min).
- **`prometheus://host:porta`** — `PrometheusMetricsSource` (timeout + retries), relógio e
  sleep reais; `run_forever` se não houver `--ticks`.

### Exit codes

| Código | Significado |
|---|---|
| `0` | Terminou sem escalada |
| `1` | Argumentos inválidos, fonte inexistente/inválida ou falha na execução (fail-closed: falha ≠ "limpo") |
| `3` | Houve escalada: proposta SIMULADA, aprovação humana obrigatória |

(`2`, regressão TNR, é do pipeline V0/V1 e não é emitido pelo `--monitor`.)

## Slack

A escalada notifica via `build_monitor_slack_notifier()`
(`interfaces/http/notifications/slack.py`). **Dry-run por padrão.** Só envia de verdade com
`AIOPS_MONITOR_SLACK_REAL=1` **e** `SLACK_BOT_TOKEN` definido; canal em `SLACK_CHANNEL`
(padrão `aiops-monitor`). Sem as duas variáveis, nada é postado.

## Demo

`scripts/run_continuous_demo.py` gera o cenário HPA (seed 42) em diretório temporário,
reproduz a linha do tempo e imprime ticks, primeiro alerta, lead time antes de
`MAXED_OUT_AT` e a escalada. Na seed 42: 1º alerta em 22/11 15:38, ~189 min antes de
`MAXED_OUT_AT` (18:47) na demo de replay; `executed_infra=False`; exit 3.
Nunca grava em `output/`.

## Métricas do backtest

`scripts/backtest_continuous.py` (relatório em [`vigia-barato-backtest.md`](vigia-barato-backtest.md)),
5 seeds a partir de 42: **recall 0,85 (17/20)**, **precision 1,00**, 0 falsos positivos em
flat/ruído puro, lead time médio 20,8 min nos detectados (HPA seed 42: 59 min no backtest).
Valores simulados — não generalizam para tráfego real.

## Limitações e trabalho futuro

- HPA nas seeds 44–46 **não** é detectado (recall HPA 2/5): limiar único de EWMA; o recall
  global vem de séries sintéticas com tendência forte.
- Calibração específica do cenário HPA; outras métricas usam padrões genéricos.
- Sem persistência de estado entre execuções; sem alta disponibilidade do monitor.
- Prometheus testado apenas contra mocks/ambiente local, não contra produção.
- Futuro: limiar por métrica/calibração automática, execução real mediada por aprovação,
  validação em dados reais.

## Perfil de tuning (`--profile`)

`auto` (padrão) aplica o tuning do cenário HPA (EWMA 0.9×request, sustentado 80%,
ETA 60 min) só em replay de CSV que contenha a métrica
`container_memory_working_set_bytes`. `hpa` força esse tuning; `generic` nunca o
aplica (usa os defaults do monitor), mesmo num CSV com a métrica de memória.

```bash
python main.py --monitor --source csv://dados.csv --profile generic --no-llm
```
