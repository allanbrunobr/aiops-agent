# Improvement Progress — execução do feature_list.json

Registro por fase conforme regra 7 do plano (`docs/improvement-agent-prompt.md`).
Cada entrada: done/skipped/blocked + motivo + testes adicionados + diffs de snapshot justificados.

## Contexto: verificação da auditoria (2026-11-17)

Antes de executar, os 32 achados do `feature_list.json` (auditoria de agente externo) foram
verificados contra o código real: 25 confirmados, 7 parcialmente confirmados, 0 falsos.
As descriptions foram corrigidas no próprio JSON; cada feature tem `verification_note`
com o delta vs auditoria original. Destaques: fases 2b/2c/2d/3 do plano de refatoração
já estavam executadas (F5-1 re-escopada); `MitigationAction` já é tipo único;
`_check_tnr` do MCP e `execute_with_safety` são código morto.

---

## Fase 0 — Rede de proteção (CI/lint/docs) — DONE (2026-11-17)

### F0-1 Ruff + mypy — done
- `pyproject.toml`: `[dependency-groups] dev` (ruff, mypy, pytest-cov); `[tool.ruff]`
  select padrão + imports (`E4,E7,E9,F,I`); `[tool.mypy]` type-check somente
  `domain/`, `application/`, `core/` (override permissivo `module="*"`).
- **Decisão registrada:** `E402` ignorado globalmente — entrypoints carregam
  `.env.local`/`sys.path` antes dos imports de propósito (padrão do projeto,
  32 arquivos) e testes usam `importorskip`. Justificativa no próprio pyproject.
- Sweep mecânico: `ruff check --fix` (416 fixes: imports ordenados, F401, F541)
  + 25 correções manuais (variáveis mortas, nomes ambíguos `l`) + `ruff format`
  (182 arquivos). Sem mudança de comportamento pretendida no sweep.
- **2 bugs latentes reais descobertos pelo lint, corrigidos com teste primeiro:**
  1. `interfaces/demo/server.py`: `_maybe_notify_slack` atribuía
     `_incident_start_wall_time` sem `global` → `UnboundLocalError` no branch
     `incident.resolved`, derrubando o tick loop. Fix: declaração `global`.
     Teste: `tests/test_demo_resolved_notification.py` (falhava com
     UnboundLocalError antes do fix; passa depois).
  2. `main.py`: anotações `"IncidentState"` sem nome importável no módulo
     (F821; `typing.get_type_hints` quebraria). Fix: import module-level.
- `core/logging.py`: processador `_redact_secrets` tipado com tipos canônicos
  do structlog (`WrappedLogger`/`EventDict`) — único erro mypy nos módulos puros.
- CI: job `lint` novo em `.github/workflows/ci.yml`; espelhado em
  `scripts/ci-local.sh lint` (incluído no `all`).
- Aceite: `ruff check .` ✓, `ruff format --check .` ✓ (269 arquivos),
  `mypy domain application core` ✓ (14 arquivos, 0 erros).

### F0-2 Cache pip + coverage — done (escopo corrigido)
- Verificação: 2 dos 3 jobs já tinham `cache: pip`; adicionado ao `unit-tests`
  (único sem cache) e ao novo job lint.
- Coverage: `pytest-cov` no job unit (CI + ci-local), `--cov --cov-report=term-missing
  --cov-report=xml`, SEM gate de threshold.Resultado: 63% (21.592 stmts).
- `.gitignore`: `.mypy_cache/`, `.ruff_cache/`, `.coverage`, `coverage.xml`, `htmlcov/`.

### F0-3 Docs obsoletos + CONTRIBUTING — done
- `docs/project-overview.md`: números reais (152 source files + 95 test files,
  ~57k linhas Python, ~1.370 testes, 28 deps); árvore de diretórios atualizada
  (domain/application/adapters/blueprint/interfaces/auth/core/app/config/rust/infra/validation).
- `CONTRIBUTING.md`: setup com uv (venvs uv não têm pip), dev-deps via
  `uv pip install --group dev` (comando verificado), seção Lint & Type Check
  e réplica local do CI. Nenhum comando documentado inexistente.

### Verificação da fase
- `scripts/ci-local.sh lint` ✓
- `scripts/ci-local.sh unit` ✓ (681 passed + coverage 63%)
- `scripts/ci-local.sh` (completo: lint + unit + integration/snapshot + rust) ✓
  **1372 passed, 10 skipped**
- Smokes: V0 `--no-llm --no-rag` exit 3 (approval-needed, contrato preservado);
  V1 `--pipeline-v1` exit 0 (resolved).
- **Diffs de snapshot: nenhum** (suíte snapshot verde sem `--snapshot-update`).
- Nota de ambiente: `scripts/build_rust.sh` falha em venv uv (sem pip);
  workaround local `maturin develop --uv` (CI não afetado — usa maturin build + pip).

### Testes adicionados na fase
- `tests/test_demo_resolved_notification.py` (1 teste, regressão do UnboundLocalError).

---

## Fase 1 — Bugs do caminho padrão (V0) — DONE (2026-11-17)

Cada bug: teste de regressão escrito ANTES do fix, visto falhar, corrigido, visto passar (regra 2).

### F1-1 Mapa FAISS corrompido após load do cache — done
- Fix: `load_index` reconstrói `_faiss_to_chunks` a partir dos chunks (dedup
  determinístico `dict.fromkeys`, mesma ordem da indexação). Guarda
  `ntotal == len(unique_texts)` para não afetar a subclasse `WithHistory`
  (indexa duplicatas e usa fallback posicional, correto para ela).
  Formato do cache inalterado — caches antigos também são corrigidos no load.
  Caminho de falha do load agora reseta o mapa (`None`).
- Teste: `TestCacheMappingRoundTrip` em `tests/test_rag_persistence.py`
  (build com textos duplicados → save → load → mesmo mapa E mesmos
  chunks/ranking na busca). Falhava com `None != [[0,1],[2]]` antes do fix.

### F1-2 for/else na sanitização de traces + mutação in-place — done
- Fix: `sanitize_traces` conta clean por span via flag `contaminated`
  (operation OU attributes) — spans sem attributes também contam como clean;
  `sanitize_metrics`/`sanitize_traces` clonam via `dataclasses.replace`
  (attributes dict copiado) — input nunca mutado, semântica unificada com logs.
- Testes: `TestSanitizeTracesCountingAndCloning` em `tests/test_safety.py`
  (3 testes: contaminado não conta clean; span limpo sem attributes conta;
  input intacto após `sanitize_all`). Os 3 falhavam antes do fix.
- Callers verificados: todos usam o retorno (main.py, safety_nodes, conftest) —
  ninguém dependia da mutação in-place.

### F1-3 Estágio preditivo sobre dados errados — done
- Fix em `pipeline/predictive_stage.py`:
  1. slope EWMA agrupado POR MÉTRICA (série = anomalias da mesma métrica entre
     serviços, ordenadas por timestamp); sinal = maior slope ascendente;
     grupos com 1 ponto não geram tendência (honesto: 1 ponto não é série);
  2. `duration_ms` ← `total_duration_ms` (campo real de SlowCallPath);
  3. `Span.operation` ← `" -> ".join(path)` (str, não list);
  4. `start_time` ← timestamp real do trace; logs sintéticos distribuídos
     uniformemente entre `first_seen`/`last_seen` do template (datetimes reais).
- Testes: `TestPredictiveStageDataFidelity` em `tests/test_predictive_stage.py`
  (4 testes com spies nos analyzers; 3 falhavam antes do fix). `FakeSlowTrace`
  atualizado para o shape real de `SlowCallPath`.
- **Decisão registrada (regra 9):** com anomalias deduplicadas a 1 por
  (service, metric_name), métricas com 1 ponto só não geram mais sinal de
  tendência — o sistema é honesto sobre não ter série temporal suficiente.

### F1-4 Key mismatch weight vs chunk_weight — done
- Fix: `rag/reranker.py` lê `metadata.get("chunk_weight")` (key escrita pelo
  indexer). Downweight de histórico (0.5) agora aplicado na fusão.
- Testes: `TestChunkWeightKeyAlignment` em `tests/test_reranker.py` (2 testes:
  sinal lido do metadata + ranking do downweighted abaixo do idêntico).
  Helper `_make_chunk` dos testes alinhado para escrever `chunk_weight`.

### F1-5 Foreign keys nunca ativadas — done
- Fix: `PRAGMA foreign_keys=ON` em `_connect()` (por conexão, fora de transação).
- **Ripple effect verificado:** V0/V1 salvam outcomes com `skill_id="default"`
  quando nenhuma skill é roteada; `seed_default_skills()` roda ANTES nos dois
  fluxos (main.py:517, pipeline_nodes.py:125) → linha "default" existe → FK não
  quebra produção. O `except Exception: pass` best-effort em main.py:724
  cobre o save (será tratado em F6-5: logar erro estruturado).
- Testes: `TestForeignKeyEnforcement` em `tests/test_skill_evolution.py`
  (3 testes: PRAGMA=1 por conexão; outcome órfão → IntegrityError; outcome com
  skill existente aceito). 2 falhavam antes do fix.

### Verificação da fase
- `ruff check .` ✓, `ruff format --check .` ✓ (270 arquivos), mypy ✓
- unit: **715 passed**; integration/snapshot: **138 passed**
- Smokes: V0 exit 3, V1 exit 0 (contrato preservado)
- **Diffs de snapshot: nenhum** — as correções não alteraram os outputs
  capturados nos cenários de snapshot.
- CI completo (`scripts/ci-local.sh`): registrado no commit da fase.

### Testes adicionados na fase
- 13 testes de regressão (1 F1-1 + 3 F1-2 + 4 F1-3 + 2 F1-4 + 3 F1-5).