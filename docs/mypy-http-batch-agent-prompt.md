# AIOPS-99 lote 2 — Higiene mypy: interfaces/http (APENAS interfaces/http/)

Worker Claude: repo `/Users/allanbrunom5/Projetos/pessoais/aiops-agent` (branch main). Leia PRIMEIRO `claude-progress.txt`, `pyproject.toml` ([tool.mypy]), `scripts/ci-local.sh:79`, `.github/workflows/ci.yml:47`, `git log --oneline -5`.

## ESCOPO FECHADO: somente `interfaces/http/`. NÃO toque interfaces/mcp, interfaces/cli, validation/, app/, auth/ nem outro pacote. Over-scoping = FALHA.
Lote 1 (auth) já feito (commit d698b3f). Gate atual = `domain application core pipeline safety data agents tools rag auth`. Override `module="*" ignore_errors=true` suprime transitivos não limpos.

## Estado REAL (verifique com /tmp/strict-mypy.ini ignore_errors=False)
`.venv/bin/mypy --config-file /tmp/strict-mypy.ini interfaces/http` mostra 39 erros, mas só **16 são de interfaces/http/** (os outros são validation/mcp importados — FORA deste lote, ficam suprimidos pelo override "*"). Foco nos 16 own, em 7 arquivos:

1. **`interfaces/http/metrics/pipeline.py`** (6 erros arg-type): `_percentile` espera `list[int | float]` mas recebe `list[int]`. **Correção**: alargue o parâmetro de `_percentile` para `Sequence[int | float]` (de `collections.abc`) ou `list[float]` com coerção; `list[int]` é compatível com `Sequence[int|float]` (covariante). NÃO mude a lógica de cálculo.
2. **`interfaces/http/webhook/router.py`** (2 union-attr): `DedupStore | None` sem guard antes de `.release`. **Correção**: guard `if self._dedup is not None:` (ou cast/assert conforme o fluxo já garante). Leia o contexto; preserve comportamento.
3. **`interfaces/http/webhook/errors.py`** (1 arg-type): handler registrado em `add_exception_handler` tipado `Callable[[Request, WebhookError], ...]` mas Starlette espera `Callable[[Request, Exception], ...]`. **Correção**: alargue o parâmetro de exceção do handler para `Exception` (e faça cast/narrow interno para WebhookError se precisar dos campos), ou registre com o tipo aceito. Preserve o comportamento do handler.
4. **`interfaces/http/routes/a2a.py`** (1 arg-type): `build_pipeline(config)` recebe `Any | None`, espera `AppConfig`. **Correção**: guard `if config is None: raise/return` ou cast para AppConfig no ponto onde já é garantido. Leia o fluxo.
5. **`interfaces/http/notifications/slack.py`** (1 union-attr): `AsyncWebClient | None` sem guard antes de `.chat_postMessage`. **Correção**: guard `if self._client is not None:` (ou o padrão já usado no arquivo). Preserve comportamento (dry-run quando None).
6. **`interfaces/http/handlers/false_positive.py`** (3 attr-defined): parâmetro tipado `object` usado como store (`.mark_pattern_negative`, `.get_all_patterns`, `.find_by_service_and_cause`). **Correção**: tipe o parâmetro com a classe real do store (leia de onde vem — provavelmente `PatternStore`/`IncidentMemoryDB`/similar; import em TYPE_CHECKING se circular). NÃO use Any.
7. **`interfaces/http/app.py`** (2 erros): `Path(object)` → tipe o valor como `str | PathLike[str]` (leia a origem); `object._build_prediction_blocks` → tipe o objeto com a classe real que tem esse método. Leia o contexto.

## Estender o gate (só depois de interfaces/http own strict-clean)
- `pyproject.toml`: adicione `"interfaces.http", "interfaces.http.*"` à lista `ignore_errors = false`. **MANTENHA** `module="*" ignore_errors=true`.
- `scripts/ci-local.sh:79` e `.github/workflows/ci.yml:47`: comando → `mypy domain application core pipeline safety data agents tools rag auth interfaces/http`.
- Verifique `.venv/bin/mypy domain application core pipeline safety data agents tools rag auth interfaces/http` → **Success** (erros de validation/mcp importados ficam suprimidos pelo "*").
- Confirme que os 16 own sumiram: `.venv/bin/mypy --config-file /tmp/strict-mypy.ini interfaces/http 2>&1 | grep -c "interfaces/http/.*error"` → 0.

## Regras INVIOLÁVEIS
- ZERO mudança de comportamento. Suíte unit **2169 passed**, 0 failed.
- Sem `# type: ignore` genérico nem `Any` indiscriminado; `isinstance`/`cast`/guards None/Protocol/TYPE_CHECKING ok.
- NÃO regen snapshots (se algum mudar, PARE e reporte).
- Sem deps novas. Nada gravar em `output/` (tmp_path). Confirme file:line.
- SOMENTE interfaces/http/. interfaces/mcp+cli, validation, app = lotes futuros.

## Verificação LOCAL M5 (antes do commit)
- strict interfaces/http own = 0; gate estendido Success.
- `.venv/bin/python -m pytest tests -m unit -q` → 2169 passed, 0 failed.
- `.venv/bin/python -m pytest tests -m snapshot -q` → 17 passed, INALTERADOS.
- `.venv/bin/ruff check .` → All checks passed.
- `.venv/bin/python main.py --no-llm --no-rag --output-dir /tmp/http-smoke; echo exit=$?` → 3.
- NÃO use `scripts/ci-local.sh unit` nem M1.

Ao terminar: commit incluindo `AIOPS-99` (ex: `chore(higiene): mypy limpo em interfaces/http + gate estendido (AIOPS-99 lote 2)`). Setar `feature_list.json[0].passes=true` SOMENTE com own strict-clean + gate verde. Reporte números.