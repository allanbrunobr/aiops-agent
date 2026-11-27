# AIOPS-99 lote 3 — Higiene mypy: interfaces/mcp + interfaces/cli (APENAS esses 2)

Worker Claude: repo `/Users/allanbrunom5/Projetos/pessoais/aiops-agent` (branch main). Leia PRIMEIRO `claude-progress.txt`, `pyproject.toml` ([tool.mypy]), `scripts/ci-local.sh:79`, `.github/workflows/ci.yml:47`, `git log --oneline -5`.

## ESCOPO FECHADO: somente `interfaces/mcp/` e `interfaces/cli/`. NÃO toque validation/, app/, interfaces/demo, interfaces/http, auth/ nem outro pacote. Over-scoping = FALHA.
Lotes 1 (auth, d698b3f) e 2 (interfaces/http, 980621c) já feitos. Gate atual = `domain application core pipeline safety data agents tools rag auth interfaces/http`. Override `module="*" ignore_errors=true` suprime transitivos não limpos (validation/scenarios ~100 erros = lote 4, FORA deste).

## Estado REAL (/tmp/strict-mypy.ini ignore_errors=False)
`.venv/bin/mypy --config-file /tmp/strict-mypy.ini interfaces/mcp interfaces/cli` mostra 104 erros, mas só **4 são OWN de mcp/cli** (os outros ~100 são validation/scenarios importados — ficam suprimidos pelo "*"). Foco nos 4 own, em 2 arquivos:

1. **`interfaces/cli/args.py:240`** (1 return-value): função retornando `Any` mas declarada `str | None`. **Correção**: anote/cast o retorno para `str | None` (leia o que retorna; se é `getattr`/dict access, cast ou narrow). NÃO mude comportamento.
2. **`interfaces/mcp/tools.py:259`** (1 no-any-return): função retornando `Any` mas declarada `bool`. **Correção**: `return bool(...)` ou cast para bool no ponto de retorno. Leia o contexto.
3. **`interfaces/mcp/tools.py:474`** (2 operator): `latest < oldest` — `latest` é `str`, `oldest` é `int | None`. **Correção**: leia o contexto (provavelmente comparação de timestamps/idades com tipos inconsistentes). Narrow/guard para tipos comparáveis coerentes SEM mudar a lógica de comparação. Se `oldest` pode ser None, guard `if oldest is not None`. Se os tipos deveriam ser iguais, corrija a origem da tipagem (não force cast cego). Preserve o comportamento real.

## Estender o gate (só depois de mcp/cli own strict-clean)
- `pyproject.toml`: adicione `"interfaces.mcp", "interfaces.mcp.*", "interfaces.cli", "interfaces.cli.*"` à lista `ignore_errors = false`. **MANTENHA** `module="*" ignore_errors=true`.
- `scripts/ci-local.sh:79` e `.github/workflows/ci.yml:47`: comando → `mypy domain application core pipeline safety data agents tools rag auth interfaces/http interfaces/mcp interfaces/cli`.
- Verifique esse comando → **Success** (validation importado fica suprimido pelo "*").
- Confirme own = 0: `.venv/bin/mypy --config-file /tmp/strict-mypy.ini interfaces/mcp interfaces/cli 2>&1 | grep -cE "interfaces/(mcp|cli)/.*error"` → 0.

## Regras INVIOLÁVEIS
- ZERO mudança de comportamento. Suíte unit **2169 passed**, 0 failed.
- Sem `# type: ignore` genérico nem `Any` indiscriminado; `cast`/guards None/`bool()` ok.
- NÃO regen snapshots (se algum mudar, PARE e reporte).
- Sem deps novas. Nada gravar em `output/` (tmp_path). Confirme file:line.
- SOMENTE interfaces/mcp + interfaces/cli. validation/app/demo = lotes futuros.

## Verificação LOCAL M5 (antes do commit)
- strict mcp/cli own = 0; gate estendido Success.
- `.venv/bin/python -m pytest tests -m unit -q` → 2169 passed, 0 failed.
- `.venv/bin/python -m pytest tests -m snapshot -q` → 17 passed, INALTERADOS.
- `.venv/bin/ruff check .` → All checks passed.
- `.venv/bin/python main.py --no-llm --no-rag --output-dir /tmp/mcp-smoke; echo exit=$?` → 3.
- NÃO use `scripts/ci-local.sh unit` nem M1.

Ao terminar: commit incluindo `AIOPS-99` (ex: `chore(higiene): mypy limpo em interfaces/mcp+cli + gate estendido (AIOPS-99 lote 3)`). Setar `feature_list.json[0].passes=true` SOMENTE com own strict-clean + gate verde. Reporte números.