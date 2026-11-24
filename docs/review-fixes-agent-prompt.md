# Spec autoritativa — correções do review adversarial (RF1–RF3)

Origem: `docs/review-qwen-result.md` (review cético do Vigia Barato + CLI operável).
Três findings acionáveis. **Código pelo Claude; verificação LOCAL no M5.**

## ⛔ REGRA DE VERIFICAÇÃO (sobrepõe qualquer nota antiga)

Verifique **AQUI no M5**, com o venv do projeto, **direto**:

```
.venv/bin/python -m pytest tests -m unit -q          # suíte completa (leve, ~20s)
.venv/bin/python -m pytest tests -m slow -q          # para RF3 (benchmark)
.venv/bin/ruff check .
.venv/bin/mypy domain application core
```

- **NÃO use `scripts/ci-local.sh unit`** — ele cria um venv fresco em /tmp (pesado) e
  o hook `m5-guard` bloqueia no M5. Foi isso que fez as tarefas OP2/OP3 "deferirem"
  e falharem em loop. O comando direto acima é leve e roda aqui (1961 passed).
- **NÃO vá pro M1 / ssh / rsync.** Estas tarefas são stdlib/CLI/teste, rodam aqui.
- Escreva o teste que **falha antes** do fix, depois corrija, depois rode a suíte
  completa direta, marque `passes:true` e commite. **UMA sessão basta por tarefa.**
- Regra silent-failure: nenhum `except X: pass` sem log (`tests/test_silent_failures.py`).
- Nada de infra real; TNR `simulated`; nada gravar em `output/` do repo (use `tmp_path`).
- Sem dependências novas. Não quebrar V0/V1 (smokes exit 3).

Assinaturas verificadas (não re-descubra):
- `interfaces/cli/exit_codes.py`: `EXIT_SUCCESS=0`, `EXIT_ERROR=1`,
  `EXIT_REGRESSION=2`, `EXIT_APPROVAL_NEEDED=3`.
- `interfaces/cli/monitor_runner.py:106 def run_monitor(args, *, sleep=None) -> int`.
- `interfaces/cli/args.py`: `build_arg_parser()`; bloco monitor termina em
  `--monitor-service` (~:132-137) antes de `return parser`.
- `tests/test_monitor_runner.py`: importa `from interfaces.cli import monitor_runner`,
  `from interfaces.cli.args import _validate_args, build_arg_parser`, fixtures
  `hpa_csv`, `stub_diagnose`.

---

## RF1 — LOW-1: Ctrl+C durante a subida não pode dar UnboundLocalError

**Bug:** `interfaces/cli/monitor_runner.py:152-153`. O handler
`except KeyboardInterrupt:` imprime mas **não retorna**; o fluxo cai em
`if monitor.escalations:` (:158). Se o Ctrl+C ocorrer antes de
`monitor = _PrintingMonitor(...)` (:139) — durante `_build_source`, `_replay_times`,
`load_config` ou `make_blueprint_diagnose` — `monitor` está unbound →
`UnboundLocalError` (traceback, exit não-controlado).

**Fix:** adicionar `return EXIT_SUCCESS` logo após o `console.print(...)` no
`except KeyboardInterrupt` (Ctrl+C = saída limpa 0, independente de `monitor` estar
bound). Manter o log (silent-failure guard).

**Teste (escreva ANTES, deve falhar com UnboundLocalError):** em
`tests/test_monitor_runner.py`, `test_keyboard_interrupt_during_startup_returns_clean`:
monkeypatch de `monitor_runner._build_source` (ou `_replay_times`) para lançar
`KeyboardInterrupt`; chamar `run_monitor(args)` com args válidos de monitor;
assert `== EXIT_SUCCESS` e que **não** lança `UnboundLocalError`.

**Aceite:** Ctrl+C na subida → exit 0 limpo; testes existentes passam; ruff/mypy ok.
**Write-set:** `interfaces/cli/monitor_runner.py`, `tests/test_monitor_runner.py`.

---

## RF2 — LOW-2: tuning HPA explícito via `--profile` (auto|hpa|generic)

**Problema:** `interfaces/cli/monitor_runner.py:129` `if replay and _is_hpa(source):`
aplica tuning HPA (`0.9*512Mi`, sustained 80%, ETA 60) a **qualquer** CSV que tenha a
métrica `container_memory_working_set_bytes`, mesmo não sendo o cenário HPA.
Heurística implícita.

**Fix (backward-compatible):**
1. `interfaces/cli/args.py`: novo `--profile` após `--monitor-service`,
   `choices=["auto","hpa","generic"]`, `default="auto"`, help explicando:
   `auto` = detecta HPA por nome de métrica (comportamento atual); `hpa` = força
   tuning HPA; `generic` = nunca aplica tuning HPA.
2. `interfaces/cli/monitor_runner.py`: ler `profile = getattr(args, "profile", "auto")`.
   Aplicar o bloco de tuning HPA quando `profile == "hpa"` **ou**
   (`profile == "auto"` e `replay` e `_is_hpa(source)`). `generic` → nunca.
   **Default `auto` mantém o comportamento atual** (smoke MON=3 intacto).
3. `docs/continuous-monitor.md`: documentar `--profile` (1 parágrafo + exemplo).

**Testes (ANTES):** `tests/test_args.py` — `--profile` parseia, default `auto`,
valor inválido rejeitado. `tests/test_monitor_runner.py` — `--profile generic` num
CSV HPA **não** aplica tuning (ex.: threshold EWMA fica no default, não `0.9*req`);
`--profile hpa` força tuning mesmo sem `_is_hpa` verdadeiro.

**Aceite:** default `auto` inalterado; `generic` desliga; `hpa` força; smokes
V0/V1/MON exit 3; ruff/mypy ok.
**Write-set:** `interfaces/cli/args.py`, `interfaces/cli/monitor_runner.py`,
`tests/test_args.py`, `tests/test_monitor_runner.py`, `docs/continuous-monitor.md`.

---

## RF3 — INFO-1: benchmark de perf só roda em build release (skip em dev)

**Problema:** `tests/rust/test_trace_predictor.py::TestPercentileBenchmark::test_rust_percentile_3x_mais_rapido_que_python`
asserta `speedup >= 3.0`. O próprio docstring diz que requer
`maturin develop --release`; o build de dev (usado para iteração rápida) não atinge
3x → falha constante na suíte slow. **Não é bug de correctness.**

**Fix:** tornar o teste condicional ao build release. Adicionar
`@pytest.mark.skipif(not os.environ.get("AIOPS_RUST_RELEASE"), reason="benchmark requer maturin develop --release; defina AIOPS_RUST_RELEASE=1 para rodar")`
(importar `os`). Assim: dev → **skipped** (suíte slow verde); release com
`AIOPS_RUST_RELEASE=1` → roda e asserta 3x. Atualizar o docstring para explicar o
env. **Não** remover o teste nem o assert 3x (o guard continua existindo em release).

**Nota (documentar, não alterar CI nesta tarefa):** o job rust-build da CI deve
buildar `--release` e exportar `AIOPS_RUST_RELEASE=1` para rodar o benchmark oficial.
Registrar isso no docstring/`docs/` como follow-up; não editar `.github/` aqui.

**Aceite:** `.venv/bin/python -m pytest tests -m slow -q` → 0 failed (perf skipped em
dev); com `AIOPS_RUST_RELEASE=1` o teste roda; ruff ok.
**Write-set:** `tests/rust/test_trace_predictor.py` (+ nota em `docs/` se preciso).

---

## Ordem e dependências

RF1 (none) → RF2 (deps RF1, ambos editam monitor_runner) → RF3 (none, independente).
Cada tarefa: teste falha antes → fix → suíte completa direta verde → `passes:true` →
commit com mensagem `fix(...): RFn ...`. Verificação final do coordenador: unit +
slow + smokes V0/V1/MON + ruff + mypy.