# AIOPS-99 lote 5 (FINAL produção) — fechar gate: app/demo + --check-untyped-defs + 2 residuais

Worker Claude: repo `/Users/allanbrunom5/Projetos/pessoais/aiops-agent` (branch main). Leia PRIMEIRO `claude-progress.txt`, `pyproject.toml` ([tool.mypy]), `scripts/ci-local.sh:79`, `.github/workflows/ci.yml:47`, `git log --oneline -5`.

## Contexto
Lotes 1-4 feitos (auth d698b3f, interfaces/http 980621c, interfaces/mcp+cli fac2322, validation 5939763). Gate atual (default mypy) = `domain application core pipeline safety data agents tools rag auth interfaces/http interfaces/mcp interfaces/cli validation` → 149 files Success. `app/` e `interfaces/demo/` já estão limpos (default). Override `module="*" ignore_errors=true` ainda presente.

## Objetivo: gate de PRODUÇÃO completo e mais estrito
Medição real com `--check-untyped-defs` (corpos de funções não tipadas): só **2 erros** em todo o source de produção:
1. **`tools/jira_tracker.py:90`** `[no-any-return]` Returning Any from function declared to return `bool` (dentro de `is_available`). **Correção**: `return bool(...)` no ponto de retorno (leia o contexto; preserve comportamento).
2. **`interfaces/demo/engine.py:160`** `[no-any-return]` Returning Any from function declared to return `IncidentResponse`. **Correção**: `cast(IncidentResponse, ...)` ou anote a origem para retornar o tipo correto (leia o contexto; NÃO mude comportamento).

## Passos
1. Corrija os 2 erros acima (type-only, behavior-preserving).
2. **Adicione `app/` e `interfaces/demo/` ao gate**: `pyproject.toml` lista `ignore_errors = false` += `"app", "app.*", "interfaces.demo", "interfaces.demo.*"`.
3. **Ative `--check-untyped-defs` no gate**: adicione `check_untyped_defs = true` na seção `[tool.mypy]` do `pyproject.toml`. (O override `module="*" ignore_errors=true` continua suprimindo tests/scripts/transitivos não-gate; os pacotes do gate com `ignore_errors=false` passam a ter corpos não tipados verificados.)
4. **Atualize o comando do gate**: `scripts/ci-local.sh:79` e `.github/workflows/ci.yml:47` → `mypy domain application core pipeline safety data agents tools rag auth interfaces/http interfaces/mcp interfaces/cli validation app interfaces/demo`.
5. **MANTENHA** `module="*" ignore_errors=true` (tests/ ~450 e scripts/ ~34 erros são escopo separado, NÃO deste lote).

## Verificação (TUDO verde antes do commit)
- `.venv/bin/mypy domain application core pipeline safety data agents tools rag auth interfaces/http interfaces/mcp interfaces/cli validation app interfaces/demo` → **Success** (com check_untyped_defs=true ativo, ~151 files).
- Confirme os 2 erros sumiram: `.venv/bin/mypy --check-untyped-defs tools interfaces/demo` → Success.
- `.venv/bin/python -m pytest tests -m unit -q` → **2169 passed**, 0 failed.
- `.venv/bin/python -m pytest tests -m snapshot -q` → 17 passed, INALTERADOS.
- `.venv/bin/ruff check .` → All checks passed.
- `.venv/bin/python main.py --no-llm --no-rag --output-dir /tmp/final-smoke; echo exit=$?` → 3.
- NÃO use `scripts/ci-local.sh unit` nem M1.

## Regras INVIOLÁVEIS
- ZERO mudança de comportamento. Sem `# type: ignore` genérico nem `Any` indiscriminado; `bool()`/`cast` ok.
- NÃO regen snapshots (se algum mudar, PARE e reporte). Sem deps novas. Nada gravar em `output/` (tmp_path).
- NÃO tente limpar tests/ nem scripts/ (escopo separado). Confirme file:line.

Ao terminar: commit incluindo `AIOPS-99` (ex: `chore(higiene): gate produção completo — app/demo + check_untyped_defs + 2 residuais (AIOPS-99 lote 5)`). Setar `feature_list.json[0].passes=true` SOMENTE com gate completo verde. Reporte: 2 erros corrigidos, gate final (pacotes + check_untyped_defs), números.