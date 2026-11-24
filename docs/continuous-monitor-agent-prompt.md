# Prompt implementador — Vigia Barato OPERÁVEL (CLI `--monitor` + demo contínua)

Você é um implementador sênior neste repo (`aiops-agent`). Execute as **4 tarefas
OP1→OP2→OP3→OP4** em ordem, uma por sessão, com teste falhando antes do fix e
verificação local. O motor contínuo (Vigia Barato VB1–VB4) **já existe e está
verde**; esta fase só o torna **operável pela CLI** e demonstrável sem infra real.

## Contexto durável (NÃO reconstrua o que já existe)

- `pipeline/continuous_monitor.py`:
  - `ContinuousMonitor(source, *, interval_seconds=60, ewma_threshold=0.90,
    escalate_eta_minutes=30.0, tracker=None, metrics=None, ceilings=None,
    sustained_levels=None, window_minutes=60, step_seconds=60, sleep=time.sleep,
    clock=None, escalation=None)`.
  - `tick(now) -> MonitorTick`, `atick(now)`, `run_for(ticks) -> list[MonitorTick]`,
    `run_forever()`. `MonitorTick(timestamp, score, level: str|None,
    eta_minutes: float|None, saturation, should_escalate: bool)`.
  - `EscalationHook(diagnose, *, notify=None, audit=None, service="unknown",
    rearm_minutes=30.0)` — `diagnose: IncidentState -> IncidentState`.
- `app/monitor_setup.py:15` `make_blueprint_diagnose(app_config, *, no_llm=True)`
  → diagnóstico profundo V1 (`BlueprintEngine.run`), fail-closed.
- `interfaces/http/notifications/slack.py:932` `build_monitor_slack_notifier()`
  → notificador Slack; **real só com `AIOPS_MONITOR_SLACK_REAL=1` E
  `SLACK_BOT_TOKEN`**; caso contrário dry-run.
- Fontes (porta `application/ports/telemetry_sources.py` `MetricsSource`):
  - `tools/csv_metrics_source.py:25` `CsvMetricsSource(path: str|Path)`.
  - `tools/prometheus_connector.py:43` `PrometheusMetricsSource(...)` (timeout+retries).
- Cenário HPA `data/hpa_saturation_scenario.py`: `export_csv(path, seed=42)`,
  `METRIC_MEMORY`, `MEMORY_REQUEST_BYTES=512Mi`, `HPA_TARGET_UTILIZATION=0.80`,
  `START_TIME`, `MAXED_OUT_AT`, `RESTART_AT`, `ground_truth()`.
- **Template de wiring já pronto**: `scripts/backtest_continuous.py` `replay_hpa`
  (~linha 104) mostra exatamente como montar `ContinuousMonitor(CsvMetricsSource(...),
  metrics=[hpa.METRIC_MEMORY], ewma_threshold=0.9*hpa.MEMORY_REQUEST_BYTES,
  sustained_levels={hpa.METRIC_MEMORY: (0.80*request, request)})`. **Reutilize.**
- CLI: `interfaces/cli/args.py` `build_arg_parser()` + `_validate_args(parser, args)`;
  `main.py` `main()` despacha `if args.pipeline_v1: main_v1(args) else run_v0(args)`.
  Exit codes em `interfaces/cli/exit_codes.py`: `EXIT_SUCCESS=0`, `EXIT_ERROR=1`,
  `EXIT_REGRESSION=2`, `EXIT_APPROVAL_NEEDED=3`.
- Config: `config/loader.py` carrega `AppConfig` (`config/defaults.yaml`).

## Regras (iguais à fase Vigia Barato — leia também `docs/vigia-barato-agent-prompt.md` §regras)

1. **Teste falha antes do fix.** Escreva o teste, veja falhar, implemente, veja passar.
2. **Write-set explícito** por tarefa (listado abaixo). Não toque fora dele sem justificar.
3. **Nada de infra real.** TNR sempre `verification_mode="simulated"`; o monitor
   **propõe + postmortem**, nunca executa remediação. Fail-closed: aprovação humana
   obrigatória (`EXIT_APPROVAL_NEEDED=3` quando há escalada).
4. **Nada gravar em `output/` do repo.** `tests/conftest.py` `_forbid_writes_to_repo_output`
   falha o teste. Use `tmp_path`.
5. **Confirme `file:line`** antes de editar (os números acima são guia, não verdade absoluta).
6. **Não quebre o V0.** Smokes `main.py --no-llm --no-rag [--pipeline-v1] --output-dir <tmp>`
   devem seguir exit 3 (approval-needed).
7. **Sem dependências novas.** stdlib + o que já está em `pyproject.toml`.
8. **Camada barata NÃO chama LLM.** O runner só injeta o diagnóstico profundo via
   `EscalationHook` (que roda o V1 sob escalada); o `tick` barato permanece puro.

## ⛔ VERIFICAÇÃO — LOCAL no M5 (sobrepõe qualquer nota antiga "no M1")

Rode **aqui**, não via ssh/rsync m1-docker:
- `.venv/bin/python -m pytest tests -m unit -q`
- `scripts/ci-local.sh unit` (suíte completa + coverage)
- smoke novo: `.venv/bin/python main.py --monitor --source csv://<tmp_hpa.csv> --ticks 5 --no-llm --output-dir <tmp>`
- `.venv/bin/ruff check .` e `.venv/bin/mypy domain application core`
Marque `passes:true` após verde + commit. **Uma sessão basta por tarefa.**

---

## OP1 — Flags CLI `--monitor` + validação

**Write-set:** `interfaces/cli/args.py`, `tests/test_args_monitor.py`.

Adicione a `build_arg_parser()`:
- `--monitor` (`store_true`): modo monitoramento contínuo (Vigia Barato).
- `--source` (`str`, metavar `URL`): `csv://<caminho>` ou `prometheus://<url>`.
- `--interval` (`float`, default `60`): segundos entre ticks (> 0).
- `--ticks` (`int`, default `None`): executa N ticks e sai (demo/teste); `None` = loop infinito.
- `--monitor-service` (`str`, default `"hpa-demo"`): nome do serviço na escalada.

Em `_validate_args`:
- `--monitor` é mutuamente exclusivo com `--scenario`, `--data-source databricks`,
  `--pipeline-v1`, `--tracker` (modo próprio). Erro → exit 1 via `parser.error`.
- `--monitor` **exige** `--source`; scheme deve ser `csv` ou `prometheus` (senão erro).
- `--interval <= 0` → erro. `--ticks` se presente deve ser `> 0`.

**Testes** (`tests/test_args_monitor.py`): flags parseiam; exclusões mútuas erram;
`--monitor` sem `--source` erra; scheme inválido erra; defaults corretos.

## OP2 — Runner `run_monitor` + wiring no `main.py`

**Write-set:** `interfaces/cli/monitor_runner.py` (novo), `main.py`,
`tests/test_monitor_runner.py`.

`interfaces/cli/monitor_runner.py` → `run_monitor(args) -> int`:
1. Parse `--source`: `csv://` → `CsvMetricsSource(path)`; `prometheus://` →
   `PrometheusMetricsSource(url)`. Falha de fonte/parse → `EXIT_ERROR` (fail-closed).
2. Carregue `AppConfig` (`config/loader.py`).
3. Monte `ContinuousMonitor` reutilizando o padrão de `scripts/backtest_continuous.py`:
   para o cenário HPA (csv do `hpa_saturation_scenario`) use
   `metrics=[hpa.METRIC_MEMORY]`, `ewma_threshold=0.9*hpa.MEMORY_REQUEST_BYTES`,
   `sustained_levels={hpa.METRIC_MEMORY: (0.80*request, request)}`; caso genérico,
   defaults do `ContinuousMonitor`. `interval_seconds=args.interval`.
4. `EscalationHook(make_blueprint_diagnose(cfg, no_llm=args.no_llm),
   notify=build_monitor_slack_notifier(), service=args.monitor_service)`.
5. `monitor.run_for(args.ticks)` se `--ticks`, senão `run_forever()`.
6. Imprima cada tick via `console` (score, level, ETA, saturação, should_escalate).
7. Exit: `EXIT_APPROVAL_NEEDED` (3) se houve escalada (proposta requer aprovação
   humana); `EXIT_SUCCESS` (0) se run limpo; `EXIT_ERROR` (1) em falha de fonte.

`main.py`: antes do dispatch V1/V0, `if getattr(args, "monitor", False): return run_monitor(args)`.
Import lazy de `run_monitor` para não arrastar deps no parser.

**Testes** (`tests/test_monitor_runner.py`, `tmp_path`, clock/sleep fake):
- csv HPA (`hpa.export_csv(tmp_path/...)`) + `--ticks` limitado → detecta + escala
  antes de `MAXED_OUT_AT`, exit 3.
- csv flat/ruído → sem escalada, exit 0.
- `--source` inválido / arquivo inexistente → exit 1.
- `--no-llm` respeitado (diagnóstico heurístico, sem chamada LLM).

## OP3 — Script de demo contínua + smoke

**Write-set:** `scripts/run_continuous_demo.py` (novo),
`tests/test_continuous_demo_smoke.py` (novo).

`scripts/run_continuous_demo.py` (stdlib-only, executável):
- Gera o csv HPA num diretório temporário (`hpa.export_csv`).
- Roda o monitor sobre a linha do tempo do cenário com **sleep zero/acelerado**
  (clock fake avançando `step_seconds`), de `START_TIME+30min` até `RESTART_AT`.
- Imprime a linha do tempo: ticks, primeiro alerta, **lead time (min) antes de
  `MAXED_OUT_AT`**, escalada + proposta (simulated). Sem infra real.
- Aceita `--source csv://...` opcional para sobrescrever o csv gerado; `--seed`.
- Reutilize `run_monitor`/`ContinuousMonitor` — não duplique a matemática.

**Smoke** (`tests/test_continuous_demo_smoke.py`, marker `slow` se > 2s):
demo detecta + escala antes de `MAXED_OUT_AT`, lead time > 0, `executed_infra=False`.

## OP4 — Documentação

**Write-set:** `README.md`, `docs/continuous-monitor.md` (novo).

- `README.md`: seção "Vigia Barato — monitoramento contínuo": como rodar
  `python main.py --monitor --source csv://<arquivo> --ticks N --no-llm`,
  `--source prometheus://<url>`, o script de demo, exit codes, arquitetura de duas
  camadas (barata zero-token / profunda sob escalada), **escopo honesto**:
  proof-of-concept, dados simulados, fail-closed, nunca executa infra, aprovação
  humana obrigatória. Não alegue produção 24/7.
- `docs/continuous-monitor.md`: guia completo (fontes csv/prometheus, flags,
  escalada, gate Slack `AIOPS_MONITOR_SLACK_REAL`, métricas do backtest
  recall/precision/lead-time, limitações e trabalho futuro).

---

## Verificação final (todas as tarefas)

- `scripts/ci-local.sh unit` verde (baseline atual ~1909+ passed).
- Smokes V0/V1 exit 3 inalterados; smoke `--monitor` novo funcionando.
- `ruff check .` limpo; `mypy domain application core` limpo.
- Cada tarefa: commit próprio com mensagem `feat(cli): OP<N> ...` e `passes:true`.