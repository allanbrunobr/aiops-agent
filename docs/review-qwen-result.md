# Resultado do review adversarial — Vigia Barato + CLI operável

Revisor: cético, com execução real de testes/smokes (não inspeção superficial).
Range: `62954b0..2e51db8`. Data: 2026-11-24.

## Sumário executivo

**Veredito geral: PASS com correções menores.** O desenho fail-closed é sólido e
verificado: a camada barata não importa LLM, nada executa infra (TNR `simulated`,
`executed_infra=False`), escalada exige aprovação humana (exit 3), Slack tem gate
duplo. A matemática do ETA está correta (auto-deriva o intervalo das amostras). O
golden **não está overfitado** (limiares principados, negativos rejeitados, `mean ≥
min` corrigido), mas a amostra é pequena (5 seeds) e sintética — é sanity check, não
prova de robustez. Nenhum blocker. 1 bug low de edge-case (Ctrl+C na subida), 1
artefato de build documentado (benchmark de perf), e notas de honestidade/robustez.

Evidência de execução (M5, `.venv`):
- unit: **1961 passed / 6 skipped / 0 failed** (19.9s)
- targeted continuous: **23 passed**
- slow: **29 passed / 1 failed / 1 skipped** — a falha é benchmark de perf (ver INFO-1)
- `ruff check .`: All checks passed · `mypy domain application core`: Success (21 files)
- smokes: V0=3, V1=3, MON=3 (fail-closed) · demo: lead time **189 min**, 19 escaladas,
  `verification_mode=simulated`, `executed_infra=False`.

## Findings (por severidade)

### LOW-1 — `UnboundLocalError` se Ctrl+C durante a subida do runner
`interfaces/cli/monitor_runner.py:152` + `:158`. O handler `except KeyboardInterrupt`
imprime mas **não retorna**; o fluxo cai em `if monitor.escalations:` (:158). Se o
Ctrl+C ocorrer antes de `monitor = _PrintingMonitor(...)` (:139) — p.ex. durante
`_replay_times`, `load_config` ou `make_blueprint_diagnose` — `monitor` está unbound
→ `UnboundLocalError` (traceback, exit não-controlado) em vez de saída limpa.
**Fix:** `return EXIT_SUCCESS` no handler KeyboardInterrupt (ou `monitor = None`
antes do try + guarda `if monitor is not None and monitor.escalations`).

### MEDIUM-1 — Amostra do backtest pequena para claim de robustez
`scripts/backtest_continuous.py:167` `run_backtest(seeds=5, base_seed=42)`; golden
`config.seeds=5` (42–46). `recall=precision=1.0` sobre 5 seeds × poucos kinds é
sanity check, não evidência estatística forte. Os negativos sintéticos (flat/noise/
noise_heavy) são trivialmente separáveis. **Não é overfit** (limiares são principados:
`0.9*ceiling`, alvo 80% HPA; o backtest aceita `--seeds N`), mas o docs/golden não
deixam explícito o tamanho da amostra como limitação.
**Fix:** rodar `--seeds 50+` e reportar a variância; declarar no docs que 1.0 em 5
seeds sintéticas não generaliza para produção. (A validação em dado real segue
bloqueada — Grafana Cloudflare/SRE sem CSV.)

### LOW-2 — Auto-tuning HPA por presença de nome de métrica
`interfaces/cli/monitor_runner.py:98` `_is_hpa` consulta `hpa.METRIC_MEMORY
(container_memory_working_set_bytes)` e, se houver linhas, aplica tuning HPA
(`0.9*512Mi`, sustained 80%, ETA 60). Qualquer CSV que use esse nome de métrica
recebe o tuning HPA mesmo não sendo o cenário. Heurística aceitável para PoC, mas
implícita.
**Fix:** documentar que o tuning HPA é automático por nome de métrica; ou gate
explícito (`--profile hpa`) para evitar aplicação inadvertida.

### LOW-3 — Circularidade do backtest sintético
O gerador (`data/hpa_saturation_scenario.py`, `scripts/backtest_predictive.py`)
cria séries com padrões de falha claros; o detector é avaliado nelas. Inerente a
backtest sintético; o escopo está documentado como simulado. Não é defeito, mas
reforça MEDIUM-1: o número 1.0 mede "detecta o que foi gerado para ser detectável".
**Fix:** nenhum código; manter a honestidade do escopo e priorizar dado real.

### INFO-1 — Benchmark de perf falha no build de dev (documentado, não regressão)
`tests/rust/test_trace_predictor.py::TestPercentileBenchmark::test_rust_percentile_3x_mais_rapido_que_python`.
O próprio teste documenta: "requer `maturin develop --release`… o build de dev pode
não atingir 3x". O `.so` atual foi buildado em modo dev (durante a verificação) →
Rust não atinge 3x → falha. **Não é bug de correctness nem do Vigia Barato.**
**Fix:** buildar `--release` para o benchmark oficial; ou marcar o teste como
`xfail`/skip quando o build é dev (detectar via flag), para não poluir a suíte slow.

## Veredito por eixo

1. **Correção da matemática — PASS.** `compute_ewma_slope`
   (`pipeline/metric_predictor.py:23`) deriva `sample_interval_minutes` da mediana
   dos deltas quando None (`_derive_interval_minutes:123`) → ETA em minutos correto
   independente do `step_seconds`; guarda slope≤0/threshold≤current; clamp D1
   presente. `detect_sustained_level` (`saturation_detector.py:103`) exige média ≥
   alvo E mínimo ≥ alvo−1% teto nas últimas K amostras → porta contra ruído.
2. **Segurança / fail-closed — PASS.** `continuous_monitor.py` não importa
   `adapters.llm` (verificado); `EscalationResult.executed_infra=False` +
   `requires_human_approval=True` + `verification_mode="simulated"` invariantes;
   `run_monitor` retorna exit 3 em escalada, exit 1 em falha de fonte
   (`MonitorSourceError`), `--no-llm` propaga via `make_blueprint_diagnose`
   (`dataclasses.replace(state, no_llm=no_llm)`); Slack real só
   `AIOPS_MONITOR_SLACK_REAL=1` **E** `SLACK_BOT_TOKEN` (`slack.py:932`), senão
   dry-run. Diagnóstico/notify/audit com except logado (não derruba o vigia).
3. **Arquitetura / camadas — PASS.** `pipeline/continuous_monitor.py` importa
   `application.ports`, `core`, `data`, `domain`, `pipeline` — não `interfaces`/`app`
   (composição em `app/monitor_setup.py` + `interfaces/cli/monitor_runner.py`).
   Smokes V0/V1 exit 3 intactos.
4. **Honestidade dos testes/golden — PARTIAL.** Golden não overfitado (limiares
   principados, `mean_lead 58.45 ≥ min 10`, negativos 0 alertas, `hpa_recall 1.0`
   com lead 171–189 min). Testes verificam comportamento (23 targeted, smoke do demo
   checa lead>0 + simulated). **Mas** amostra 5 seeds sintéticas (MEDIUM-1) e
   circularidade (LOW-3) limitam o claim. 1 benchmark perf falha por build dev
   (INFO-1, documentado).
5. **Escopo honesto — PASS.** README/docs marcam proof-of-concept, dados simulados,
   fail-closed, aprovação humana; sem claim de produção 24/7. `scope` dos goldens:
   "batch… sem monitor contínuo" / "tick a tick… sem LLM".
6. **Qualidade de código — PASS.** `ruff` limpo, `mypy domain application core`
   Success, guard silent-failure verde (KeyboardInterrupt logado em
   `continuous_monitor`/runner). Sem deps novas. (Ressalva LOW-1 no handler.)

## O que está bem feito (específico)

- **Separação CSV-replay vs Prometheus-live** (`monitor_runner.py:113-141`): CSV usa
  relógio simulado (`clock=lambda: next(it)`, `sleep=lambda: None`) sobre a linha do
  tempo dos dados; Prometheus usa relógio/sleep reais. Evita o bug óbvio de
  `run_forever` com relógio real contra CSV histórico.
- **ETA auto-intervalo** (`metric_predictor.py:123`): não exige o chamador passar
  `sample_interval_minutes`; deriva dos timestamps reais → robusto a `step_seconds`.
- **Gate duplo do Slack** + dry-run por padrão: sem envio acidental.
- **Dedup/rearm da escalada** (`EscalationHook._last`/`rearm`): um incidente aberto
  gera um diagnóstico (token caro), re-arma ao normalizar ou após 30 min.
- **Porta do nível sustentado** (média E mínimo) contra falso positivo em ruído.
- **Fix de tipo** (`target_level` float separado de `level` str|None) e **fix da
  média** (só sobre seeds detectadas) — ambos verificados.

## Perguntas em aberto (só o autor responde)

1. O tuning HPA automático por nome de métrica (LOW-2) é intencional para qualquer
   CSV, ou deveria ser explícito (`--profile hpa`)?
2. Qual o plano para validação em dado real (hoje bloqueada por Grafana/SRE)? Sem
   isso, o 1.0 do golden permanece sanity check sintético.
3. O benchmark de perf (INFO-1) deve ser `xfail` em build dev, ou a CI slow deve
   buildar `--release`?