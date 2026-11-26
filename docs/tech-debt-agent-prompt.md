# Prompt — Débitos técnicos (stub Rust + amostra backtest + honestidade)

Você é um implementador autônomo. Execute **3 tarefas** na ordem da fila
(`feature_list.json`), **uma por sessão**, verificando localmente antes de marcar
`passes: true`. Código mínimo, escopo estreito, sem dependências novas.

## Regras fixas (valem para todas as tarefas)
- **Verificação LOCAL no M5** (arm64, `.venv` pronto). Use
  `.venv/bin/python -m pytest tests -m unit -q` para a suíte unit.
  **NÃO** use `scripts/ci-local.sh unit` (cria venv fresco = pesado, o hook
  m5-guard bloqueia no M5 e você vai deferrir → falha). **NÃO** vá para o M1.
- Teste novo/falhante **antes** do fix quando houver mudança de comportamento.
- Write-set explícito por tarefa; não toque fora dele. Nada de gravar em `output/`
  do repo nos testes (use `tmp_path`; ver `tests/conftest.py` `_forbid_writes_to_repo_output`).
- Fail-closed preservado; nada de infra real; não quebrar o V0.
- Confirme `file:line` antes de editar (o código pode ter mudado).
- Ao terminar: rode a verificação, marque `passes: true` na feature, e commit
  (mensagem `fix(...)`/`docs(...)` com o código da tarefa). **Uma sessão basta.**
- O card Jira correspondente (`jira_key`) é movido automaticamente pelo hook.

---

## Tarefa 1 — TD1: stub `_aiops_rust.pyi` + guards `ModuleType | None` (AIOPS-87)

**Problema (verificado):** o módulo Rust compilado `_aiops_rust` não tem stub de
tipos. O Pyright (runner primário do pi-lens) reporta `reportAttributeAccessIssue`
/ `reportOptionalMemberAccess` como 🔴 blockers em
`tests/rust/test_trace_predictor.py` (8) e nos guards de import opcional:
- `pipeline/metric_predictor.py:15-21`
- `pipeline/trace_predictor.py:20-26`

Padrão atual (ambos):
```python
try:
    import _aiops_rust as _rust
    _RUST_AVAILABLE = hasattr(_rust, "compute_ewma_slope")  # (trace: detect_latency_drift)
except ImportError:
    _rust = None
    _RUST_AVAILABLE = False
```
O `_rust = None` faz o type-checker inferir tipo conflitante (Module vs None).

**Funções realmente chamadas** (grep `_rust\.` em `pipeline/` e `tests/rust/`):
`compress_context`, `compute_ewma_slope`, `detect_latency_drift`,
`detect_taint_batch`, `detect_taint_single`, `filter_metrics`, `filter_traces`,
`parse_logs`. **Confira os exports reais** em `rust/src/lib.rs` (`#[pyfunction]`
+ `wrap_pyfunction!`) e inclua TODOS no stub, com assinaturas fiéis (params +
defaults + retorno). Assinaturas de referência (verifique no Rust):
`filter_metrics(..., sigma_threshold=3.0, baseline_ratio=0.5)`,
`filter_traces(..., percentile_threshold=95.0, max_hops=3)`,
`detect_latency_drift(baseline, current, threshold_pct=0.20)`,
`compute_ewma_slope(values, span)`, `detect_cusum(values, k, h)`.

**Fix:**
1. Criar `_aiops_rust.pyi` na **raiz do repo** (onde `import _aiops_rust` resolve),
   com as assinaturas de todas as funções exportadas. Use `list[float]`,
   `list[dict]`, etc. conforme o retorno real (inspecione o Rust + os callers).
2. Ajustar os guards nos 2 módulos para o type-checker entender
   `_rust: ModuleType | None` **sem** conflito de redefinição e **preservando o
   fallback runtime** (quando o Rust não está disponível, `_rust is None` e o
   caminho Python puro roda). Padrão sugerido (valide com mypy + Pyright):
   ```python
   from types import ModuleType
   _rust: ModuleType | None
   try:
       import _aiops_rust as _rust_mod
       _rust = _rust_mod
       _RUST_AVAILABLE = hasattr(_rust_mod, "compute_ewma_slope")
   except ImportError:
       _rust = None
       _RUST_AVAILABLE = False
   ```
   (Adapte para não disparar `no-redef`/`assignment` no mypy nem
   `reportOptionalMemberAccess` no Pyright nos pontos de uso `_rust.foo(...)` —
   pode ser necessário guard `if _rust is not None:` ou `assert _rust is not None`
   nos callers já protegidos por `_RUST_AVAILABLE`.)

**Write-set:** `_aiops_rust.pyi` (novo), `pipeline/metric_predictor.py`,
`pipeline/trace_predictor.py`, e `tests/rust/test_trace_predictor.py` se precisar
de anotação. **NÃO** edite `.pi-lens.json` (é do usuário).

**Aceite:**
- pi-lens fresh analyze → **0 blockers** em `tests/rust/test_trace_predictor.py`,
  `pipeline/metric_predictor.py`, `pipeline/trace_predictor.py`.
  Verifique: `node ~/.pi/agent/npm/node_modules/pi-lens/dist/mcp/worker.js --file=<abs> --cwd=<abs>`
  (counts.blockers == 0).
- `.venv/bin/mypy domain application core pipeline` limpo.
- `.venv/bin/ruff check .` limpo.
- Suíte unit verde; testes de paridade Rust↔Python
  (`tests/rust/test_rust_python_parity.py`) verdes; fallback Python puro continua
  funcionando (teste com `_RUST_AVAILABLE=False` se existir).

---

## Tarefa 2 — TD2: backtest com mais seeds + variância (AIOPS-85, MEDIUM-1)

**Problema (verificado):** `scripts/backtest_continuous.py:167`
`run_backtest(seeds: int = 5, base_seed: int = 42)`; `:232` `--seeds` default 5;
golden `validation/golden/continuous_backtest.json` `config.seeds=5` (42–46).
`recall=precision=1.0` sobre 5 seeds sintéticas é sanity check, não evidência
estatística. Os negativos (flat/noise/noise_heavy) são trivialmente separáveis.

**Fix:**
1. Rodar o backtest com **`--seeds 50`** (ou mais) e **regenerar o golden**
   `validation/golden/continuous_backtest.json` com `config.seeds=50`.
2. **Reportar variância** no resultado: para o lead time HPA, inclua
   `min`/`max`/`mean`/`std` (std populacional ou amostral, documente) sobre as
   seeds detectadas; e recall/precision por kind. Mantenha a correção da média
   (média só sobre seeds detectadas, `mean >= min`).
3. Atualizar `tests/test_continuous_backtest.py` (slow) para o novo golden:
   asserts de recall ≥ 0.8, precision ≥ 0.9 (ou o valor real observado, sem
   afrouxar para forçar), zero falso alarme em flat/noise, `mean >= min`.
4. **Honestidade no docs** (`docs/continuous-monitor.md`): declarar que mesmo com
   50 seeds o backtest é **sintético** e não generaliza para produção; a validação
   em dado real segue bloqueada (Grafana Cloudflare / SRE sem CSV).

**Write-set:** `scripts/backtest_continuous.py`,
`validation/golden/continuous_backtest.json`, `tests/test_continuous_backtest.py`,
`docs/continuous-monitor.md`.

**Aceite:**
- `.venv/bin/python scripts/backtest_continuous.py --seeds 50` roda e grava o
  golden com `seeds=50` + variância.
- `.venv/bin/python -m pytest tests -m slow -q` verde (backtests).
- recall ≥ 0.8, precision ≥ 0.9, zero falso alarme nos negativos; `mean >= min`.
- docs declara a limitação sintética explicitamente.
- Suíte unit verde.

---

## Tarefa 3 — TD3: documentar circularidade do backtest (AIOPS-86, LOW-3)

**Problema (verificado, review):** o gerador (`data/hpa_saturation_scenario.py`,
`scripts/backtest_predictive.py`) cria séries com padrões de falha claros; o
detector é avaliado nelas. O número 1.0 mede "detecta o que foi gerado para ser
detectável". **Não é defeito de código** — é limitação inerente a backtest
sintético. O review diz: "nenhum código; manter a honestidade do escopo".

**Fix (docs-only):** adicionar seção **"Limitações do backtest sintético"** em
`docs/continuous-monitor.md` (aproveite a seção de honestidade da Tarefa 2)
explicando:
- circularidade: gerador e detector compartilham pressupostos (o cenário é
  construído para ser detectável);
- a amostra (agora 50 seeds, Tarefa 2) ainda é sintética;
- `recall/precision = 1.0` **não** é evidência de produção;
- validação em dado real bloqueada (Grafana Cloudflare / SRE sem CSV) — é a
  prioridade para sair do sanity check.
Referencie MEDIUM-1/LOW-3 do review (`docs/review-qwen-result.md`).

**Write-set:** `docs/continuous-monitor.md` (sem mudança de código).

**Aceite:** seção explícita de limitações (circularidade + amostra + dado real);
nenhum código alterado; suíte unit verde (sanity).

---

## Verificação final (após as 3)
```bash
.venv/bin/python -m pytest tests -m unit -q          # verde
.venv/bin/python -m pytest tests -m slow -q          # backtests verdes
.venv/bin/ruff check . && .venv/bin/mypy domain application core pipeline
node ~/.pi/agent/npm/node_modules/pi-lens/dist/mcp/worker.js \
  --file="$PWD/tests/rust/test_trace_predictor.py" --cwd="$PWD"   # blockers 0
```
Smokes (opcional, confirmar V0 intacto):
`main.py --no-llm --no-rag --output-dir /tmp/td-smoke` → exit 3 (approval-needed).
