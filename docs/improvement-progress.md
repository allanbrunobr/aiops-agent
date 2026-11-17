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