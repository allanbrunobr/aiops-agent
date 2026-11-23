# Prompt do implementador — Dados fictícios do HPA + "Vigia Barato" (monitor contínuo)

> **Destinatário:** agente Claude implementador, SEM acesso à conversa de origem. Este documento é autossuficiente.
> **Repositório:** `/Users/allanbrunom5/Projetos/pessoais/aiops-agent` (Python 3.12 + extensão Rust PyO3 `_aiops_rust`, MIT, PoC).
> **ANTES de começar, leia por completo:** `ROBUSTNESS_PLAN.md` (contexto arquitetural, Eixos A–D, regras, comandos) e `docs/improvement-agent-prompt.md` (9 regras rígidas). Este prompt ESTENDE o Eixo D daquele plano com uma feature nova (o monitor contínuo) que o plano original deixou explicitamente fora de escopo (`ROBUSTNESS_PLAN.md:449`).
> **Modo de trabalho:** cada tarefa tem write-set explícito; não expandir escopo; um commit/PR por tarefa; teste que falha ANTES do fix para cada bug; confirme cada `file:line` antes de editar (linhas deslocam).

---

## 0. Contexto mínimo

- **Pipeline:** telemetria → sanitização → compressão (Drain3 logs / 3-sigma métricas / p95 traces) → RAG → diagnóstico ReAct (LLM) → proposta de mitigação → 4 camadas de safety → postmortem. Orquestração V0 em `interfaces/cli/v0_pipeline.py` (`run_v0`); V1 via `blueprint/engine.py` (`BlueprintEngine.run(pipeline, state)`).
- **Exit codes** (`interfaces/cli/exit_codes.py:8-11`): `0` sucesso, `1` erro, `2` regressão TNR, `3` aprovação humana necessária (fail-closed).
- **TNR é SIMULADO** (`verification_mode:"simulated"`): NUNCA executa infra real (contrato de `SECURITY.md`). Ação destrutiva/não verificada → aprovação humana (exit 3).
- **Hoje o disparo é por incidente** (webhook `infra/run_webhook_server.py` / CLI `main.py`): processa um lote e para. A demo (`interfaces/demo/`) roda contínua, mas sobre dados SIMULADOS. **Falta o monitor contínuo em dados reais** — é isso que este prompt constrói.

### Peças que JÁ existem (verificadas — reutilize, não recrie)

| Peça | Assinatura / local |
|------|--------------------|
| Slope EWMA + ETA | `pipeline/metric_predictor.py:21` `compute_ewma_slope(points: list[MetricPoint], span=10, threshold=None) -> (slope, eta_minutes)`. **slope é POR AMOSTRA** (`ewma_last - ewma_prev`); `eta=(threshold-current)/slope`. Aceleração Rust opcional `_aiops_rust.compute_ewma_slope`. |
| Score de risco | `pipeline/risk_scorer.py:60` `compute_risk_score(metric_signal, log_signal, latency_signal) -> float 0..100` (pesos metric 40% / log 30% / latency 30%). `MetricTrendSignal(slope, eta_minutes=None, change_point_detected=False)` (`:18`). |
| Nível de evento | `pipeline/risk_scorer.py:78` `get_event_level(score) -> "prediction.alert"(≥60) \| "prediction.warning"(≥30) \| None`. |
| Tracker stateful | `pipeline/risk_tracker.py:14` `RiskTracker(decay_rate=3, max_ticks=90, smoothing_alpha=0.35)`; `.update(raw_score)->adjusted` (bônus persistência +5/15 ticks, máx +30; decay 3x; EWMA; dead-zone <15→0); `.reset()`; `.warning_ticks`; `.smoothed_risk`. Docstring já diz "monitoramento contínuo em produção". |
| Estágio preditivo batch | `pipeline/predictive_stage.py:27` `run_predictive_stage(aggregated_report) -> PredictionReport \| None`. **GAP D-e2:** chama `compute_ewma_slope(points)` SEM threshold → ETA sempre None em produção. |
| Porta de métricas | `application/ports/telemetry_sources.py:13` `MetricsSource.query(promql, start, end, step_seconds=15) -> list[dict]`; `.get_alerts(active_only=True) -> list[dict]`. Implementações ficam em `tools/` (simulada, Databricks; adapter Prometheus real NÃO existe). |
| Padrão de loop contínuo | `interfaces/demo/analyzer.py:24` `DemoAnalyzer(enable_prediction=True, ewma_threshold=0.90).analyze(log_entries, metric_points, spans) -> dict`. Agrupa métricas por nome; `primary_metric = connection_pool_active \| memory_used_mb \| cpu_usage`; `compute_ewma_slope(span=10, threshold=ewma_threshold)` → `compute_risk_score` → `RiskTracker.update` → `get_event_level`. **Este é o padrão a reutilizar no monitor de produção.** |
| Modelos de dados | `data/models.py`: `MetricPoint(timestamp, service, metric_name, value)`, `LogEntry`, `Span`, `SimulatedIncident`, `PredictionReport`, `AggregatedReport`. |
| Gerador de cenário (padrão) | `data/memory_leak_scenario.py` (stdlib-only, `generate_logs/metrics/spans`, constantes de tempo, `register_scenario` de `data/registry.py`). Siga este padrão. |
| Notificação | `interfaces/http/notifications/slack.py` (dry-run por default; real só com opt-in explícito + token). |

### O incidente real que os dados fictícios devem reproduzir (HPA / wbia prd)

Alerta `KubeHpaMaxedOut` ativo ~18h47 (22/11). Causa: **creep de memória** (leak) → `container_memory_working_set_bytes` por pod sobe devagar ao longo de horas → utilização do HPA (`working_set / memory_request`) fica **cronicamente acima do alvo de 80%** → HPA faz scale-up e **nunca scale-down** → réplicas sobem até o **teto `maxReplicas=10`** e ficam presas → `KubeHpaMaxedOut`. Restart é paliativo (memória cai e volta a subir). Fix real: aumentar o `request` de memória (denominador), mas o leak adia o problema. **O EWMA-slope sozinho perde o platô final** (slope≈0 quando réplicas estão presas no teto) — por isso o detector de saturação (D4) é necessário.

---

## 1. TAREFA 1 — Gerador de cenário fictício do HPA *(commit próprio — fazer PRIMEIRO)*

**Goal:** materializar o incidente acima como dados sintéticos reprodutíveis, para testar o Vigia Barato e o backtest SEM depender do CSV real (que não chegou).

**Write-set:** `data/hpa_saturation_scenario.py` (novo), `data/registry.py` (registrar), `tests/test_hpa_saturation_scenario.py` (novo). **Stdlib-only** (não importar torch/faiss/sentence-transformers).

**Especificação do cenário (parâmetros concretos, seed fixa):**
- Serviço `wbia`, namespace `prd`. Métrica primária `container_memory_working_set_bytes` (por pod); secundária `container_cpu_usage_seconds` (irate). Métrica de estado do HPA: `kube_hpa_status_current_replicas` e `kube_hpa_spec_max_replicas`.
- Constantes: `MEMORY_REQUEST_BYTES = 512 * 1024**2` (512Mi); `HPA_TARGET_UTILIZATION = 0.80`; `MAX_REPLICAS = 10`; `MIN_REPLICAS = 2`; `SAMPLE_INTERVAL_S = 60`; duração total ~19h (≈1140 amostras).
- **Fase 1 (normal, ~2h):** working_set ~300Mi (util ~59%), réplicas=2, ruído gaussiano pequeno.
- **Fase 2 (creep, ~10h):** working_set sobe quasi-linearmente de 300Mi→~470Mi (util 59%→92%) com ruído; quando util cruza 80%, réplicas sobem degrau a degrau (2→3→…→10) ao longo de horas.
- **Fase 3 (saturação, ~6h):** réplicas PRESAS em 10 (platô, variância≈0); util oscila cronicamente 80–92% (nunca sustenta abaixo do alvo → sem scale-down); working_set continua subindo devagar. **`KubeHpaMaxedOut` dispara no início desta fase** (ground-truth para medir lead time).
- **Fase 4 (restart paliativo, opcional):** working_set cai abruptamente (restart) e volta a subir; réplicas permanecem no teto.
- Ground-truth rotulado: `incident_start` (início do creep), `maxed_out_at` (início da fase 3), `restart_at`. Expor como `SimulatedIncident` + atributos de rótulo.
- **Export CSV:** função `export_csv(path)` gravando `timestamp,service,metric_name,value` (uma linha por ponto) — para o `CsvMetricsSource` (Tarefa VB1) e para inspeção. NUNCA gravar em `output/` do repo nos testes (usar `tmp_path`).

**Aceite:** cenário registrado no registry; `generate_metrics()` retorna série com as 4 fases e rótulos; teste confirma (a) util cruza 80% e réplicas atingem 10, (b) platô final tem variância baixa no teto, (c) CSV roundtrip em `tmp_path`, (d) stdlib-only (sem imports pesados). `pytest tests -m unit -q` verde.

---

## 2. TAREFA 2 — Fundação preditiva (Eixo D do ROBUSTNESS_PLAN) *(commits próprios, na ordem)*

Execute **D1 → D2 → D3 → D4 → D5** exatamente como especificado em `ROBUSTNESS_PLAN.md:309-363` (write-sets e aceite lá). Resumo do que cada uma entrega e por que importa para o Vigia Barato:

- **D1** (`:311`): clamp de score de log negativo (`risk_scorer.py:45`) + baseline de latência zero não explode ratio (`trace_predictor.py:92-93`). → sinais não alarmam sobre lixo.
- **D2** (`:322`): **ETA correto** — `compute_ewma_slope` ganha `sample_interval_minutes` (ou deriva dos timestamps) e converte slope por-amostra → minutos; `current` a partir do EWMA; `predictive_stage.py:~83` passa `threshold` configurável. → **lead time real** (sem isto o "vai estourar em X min" está errado/None). Manter paridade Rust↔Python (`tests/rust/test_rust_python_parity.py:237-254`).
- **D3** (`:332`): slope relativo opcional + portões mínimos (série <N pontos / efeito <ε → sem sinal; flat+ruído não gera `prediction.alert`). → controla falso positivo do vigia contínuo.
- **D4** (`:342`): **`pipeline/saturation_detector.py`** (novo) `detect_saturation(points, ceiling) -> SaturationSignal | None` — platô no topo (variância baixa + nível ≥95% do ceiling, sustained ≥K amostras). Integrar em `predictive_stage` + `risk_scorer` (renormalizar pesos). → **detecta o platô do HPA no teto que o slope perde.**
- **D5** (`:354`): **`scripts/backtest_predictive.py`** + `validation/golden/predictive_backtest.json` + `tests/test_predictive_backtest.py` (marker `slow`, seed fixa, recall≥0.8 nas falhas fortes, precision≥0.7, zero alertas em flat/ruído). Replaya janelas (batch). → mede precisão/recall/lead-time em vez de inspeção.

**Regra:** não pule D1–D3 antes de D4/D5 (base limpa). Cada uma: teste que falha antes → fix → verde. `scripts/ci-local.sh unit` ao fim de cada; CI completo ao fim da fase.

---

## 3. TAREFA 3 — O "Vigia Barato": monitor contínuo em duas camadas *(feature nova — PRs próprios)*

**Goal:** tornar a previsão **proativa e contínua sem queimar tokens**, separando:
- **Camada barata (contínua, ZERO LLM/token):** polling de métricas a cada `interval_seconds` (default 60) + matemática pura (`compute_ewma_slope` COM threshold → `compute_risk_score` → `RiskTracker.update` → `get_event_level` + `detect_saturation`). Roda 24/7, custo só de CPU.
- **Camada profunda (sob escalada, gasta token):** só quando o risco cruza `prediction.warning`/`alert` OU `eta_minutes < escalate_eta_minutes` OU saturação detectada → dispara o pipeline de diagnóstico existente (V1 `BlueprintEngine` ou `run_v0`) sobre o serviço/janela sinalizado. O vigia NUNCA executa infra; a camada profunda propõe + aprovação humana (fail-closed, exit 3).

### VB1. Adapters de `MetricsSource` *(commit próprio)*
**Write-set:** `tools/prometheus_connector.py` (novo), `tools/csv_metrics_source.py` (novo), `tests/test_metrics_sources.py` (novo).
1. `PrometheusMetricsSource(MetricsSource)`: `query()` → HTTP GET `/api/v1/query_range` (params `query,start,end,step`), parse `data.result[].values` → `list[dict]`; `get_alerts()` → `/api/v1/alerts`. Base URL via env/config (`PROMETHEUS_URL`); timeout + retries (espelhar `tools/databricks_connector.py`, mas COM retries — ver B-e9). **Sem dependência nova** (usar http client já presente no repo).
2. `CsvMetricsSource(MetricsSource)`: lê o CSV do cenário (Tarefa 1) e responde `query()` filtrando por `metric_name`/janela — para teste offline do vigia sem Prometheus.
**Aceite:** testes com servidor HTTP mock / CSV em `tmp_path`; `query` retorna séries corretas; timeout/retry cobertos; unit verde sem torch.

### VB2. Loop de monitoramento contínuo *(commit próprio)*
**Write-set:** `pipeline/continuous_monitor.py` (novo), `config/defaults.yaml` + `config/loader.py` (novos campos), `tests/test_continuous_monitor.py` (novo).
1. `ContinuousMonitor(source: MetricsSource, *, interval_seconds=60, ewma_threshold, escalate_eta_minutes, tracker: RiskTracker | None)`: método `tick(now)` que (a) consulta a janela de métricas via `source.query`, (b) monta `MetricPoint` por métrica, (c) `compute_ewma_slope(points, span=10, threshold=ewma_threshold)` (pós-D2), (d) `compute_risk_score` + `detect_saturation`, (e) `tracker.update`, (f) `get_event_level` → retorna um `MonitorTick` (score, level, eta, saturation, should_escalate). **Reutilize a lógica de `interfaces/demo/analyzer.py`** (não duplique a matemática).
2. `run_forever()` / `run_for(ticks)`: loop com `sleep(interval_seconds)`; `tick` determinístico e testável SEM sleep (injetar relógio/`now`). Config: `monitor.interval_seconds`, `monitor.ewma_threshold`, `monitor.escalate_eta_minutes`.
3. **Camada barata não chama LLM** — asserts/testes garantem zero import de `adapters/llm` no caminho do tick.
**Aceite:** replay do CSV do HPA via `CsvMetricsSource` → o monitor emite `prediction.warning`/`alert` ANTES de `maxed_out_at` (lead time > 0); flat/ruído → nenhum alerta (pós-D3); tick determinístico com seed; unit verde.

### VB3. Escalada para a camada profunda + aviso proativo *(commit próprio)*
**Write-set:** `pipeline/continuous_monitor.py` (hook de escalada), `interfaces/http/notifications/slack.py` (reuso), `tests/test_continuous_escalation.py` (novo), `docs/`.
1. Quando `should_escalate` → montar `IncidentState`/`NormalizedAlert` para o serviço+janela e chamar o pipeline de diagnóstico existente (V1 `BlueprintEngine.run` via builders, ou `run_v0`), **em thread separada** se async (ver F3-2 `asyncio.to_thread`). O resultado é proposta + postmortem; **nenhuma execução de infra** (fail-closed).
2. Aviso proativo: emitir alerta "vai estourar em ~X min" / "saturação no teto" via Slack (**dry-run default**; real só opt-in `AIOPS_MONITOR_SLACK_REAL=1` + token). Registrar no audit que a previsão é `verification_mode:"simulated"` quando aplicável.
3. Documentar em `docs/` a arquitetura em duas camadas + economia de tokens (barata contínua sem token; profunda só sob escalada) + que o monitor NÃO substitui a aprovação humana.
**Aceite:** teste de escalada com pipeline mock → diagnóstico chamado 1x quando level≥warning, 0x em flat; Slack mock recebe aviso; nenhum caminho executa infra; unit verde.

### VB4. Backtest do monitor contínuo *(commit próprio)*
**Write-set:** `scripts/backtest_continuous.py` (novo) OU estender `scripts/backtest_predictive.py`, `validation/golden/continuous_backtest.json` (novo), `tests/test_continuous_backtest.py` (novo, marker `slow`), `docs/`.
1. Replaya o cenário HPA (Tarefa 1) tick a tick via `CsvMetricsSource` + `ContinuousMonitor`; mede **lead time** (minutos entre o 1º aviso e `maxed_out_at`), precision/recall contra os rótulos, e falsos positivos em flat/ruído. Grava golden seguindo `validation/runner.py`.
2. Teste com seed fixa asserta: lead time > 0 no HPA; recall≥0.8; zero alertas em flat/ruído puro.
**Aceite:** `pytest tests/test_continuous_backtest.py -m slow -v` verde; golden gerado; lead time reportado.

---

## 4. Regras rígidas (valem para TODAS as tarefas)

1. **Teste que falha ANTES do fix** para cada bug; teste novo para cada feature.
2. **Não misture** movimentação de arquivos com mudança de comportamento.
3. **Write-set explícito** — não tocar módulos fora dele sem registrar a expansão no PR.
4. **Nenhuma ação em infra real** (`SECURITY.md`); TNR/monitor sempre `simulated`; fail-closed: `require_human_approval: true` default → ação não-read-only sai exit 3.
5. **Nenhum teste grava em `output/` do repo** — fixture autouse `_forbid_writes_to_repo_output` (`tests/conftest.py:201-215`); usar `tmp_path` / `--output-dir tmp_path`.
6. **Confirme cada `file:line` antes de editar** (deslocam); se um achado não se confirmar, registre e pule.
7. **Não quebre o V0** (`main.py` sem `--pipeline-v1`) nem os smokes.
8. **Diffs de snapshot** exigem justificativa.
9. **Ambiguidade de produto → escolha a opção mais honesta** sobre o que o sistema realmente faz e registre a decisão (ex.: não apresentar previsão simulada como validação real).
10. **Não criar dependências novas** sem justificar; stdlib-only nos geradores de cenário.

## 5. Verificação (comandos reais)

```bash
# Unitário (sem torch/faiss — importorskip nos testes pesados)
.venv/bin/python -m pytest tests -m unit -q
scripts/ci-local.sh unit
# CI completo ao fim de cada fase
scripts/ci-local.sh
# Smokes (nunca gravar em output/ do repo — usar --output-dir tmp)
.venv/bin/python main.py --no-llm --no-rag --output-dir /tmp/smoke-v0
.venv/bin/python main.py --no-llm --no-rag --pipeline-v1 --output-dir /tmp/smoke-v1
# Backtest preditivo (D5) e do monitor (VB4)
python scripts/backtest_predictive.py
pytest tests/test_predictive_backtest.py -m slow -v
pytest tests/test_continuous_backtest.py -m slow -v
# Lint/type
ruff check . && ruff format --check . && mypy domain application core
```

**Restrição de host:** o host pesado `m1-docker` é **Intel x86_64 macOS** — torch não publica wheels Intel-Mac após 2.2.2; o stack real (`requirements.txt`: `sentence-transformers>=5.6,<6`, `torch>=2.13,<2.14`, `faiss-cpu>=1.15.1`) NÃO roda nativamente nele. Validação real-RAG/pesada deve ser: (a) CI `ubuntu-latest` (linux x86_64), (b) container Docker `linux/amd64` no Intel, ou (c) nativo arm64 no M5. **Nunca propor job nativo Intel-Mac com torch.** As tarefas deste prompt (Vigia Barato + cenário) são **stdlib-only / matemática pura** → rodam em qualquer host, sem torch.

## 6. Ordem de execução

| Ordem | Tarefa | Tipo | Commit/PR |
|-------|--------|------|-----------|
| 1 | Tarefa 1 — cenário fictício HPA | médio | próprio |
| 2 | D1 clamp sinais | quick | próprio |
| 3 | D2 ETA correto + threshold | médio | próprio |
| 4 | D3 falsos positivos | médio | próprio |
| 5 | D4 detector saturação HPA | deep | próprio |
| 6 | D5 backtest preditivo | deep | próprio |
| 7 | VB1 adapters MetricsSource | médio | próprio |
| 8 | VB2 loop ContinuousMonitor | deep | próprio |
| 9 | VB3 escalada + aviso proativo | médio | próprio |
| 10 | VB4 backtest do monitor | deep | próprio |

**Ao concluir cada tarefa:** atualize `docs/improvement-progress.md` (done/skipped/blocked + motivo + testes adicionados). Ao fim, registre em `docs/` que o monitor contínuo (antes "trabalho futuro" do Eixo D) foi implementado, com a arquitetura em duas camadas e os números de lead time do backtest.