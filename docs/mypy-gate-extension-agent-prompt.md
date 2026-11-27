# AIOPS-98 (fase 2) — Estender o gate mypy aos pacotes limpos + corrigir rag

Worker Claude: repo `/Users/allanbrunom5/Projetos/pessoais/aiops-agent` (branch main). Leia PRIMEIRO `claude-progress.txt`, `pyproject.toml` ([tool.mypy]), `scripts/ci-local.sh:79`, `.github/workflows/ci.yml:47`, `git log --oneline -6`.

## Contexto (fase 1 já feita — NÃO refaça)
Os commits `0574104`/`c0dccc5`/`11e849f` já limparam os tipos de **pipeline, safety, data, agents, tools** (verificado com `ignore_errors=False`). **rag** tem 3 erros restantes (abaixo). O gate mypy atual ainda é só `domain application core`.

## A ARMADILHA do override (LEIA COM ATENÇÃO)
`pyproject.toml [tool.mypy]` tem:
```
[[tool.mypy.overrides]]
module = "*"
ignore_errors = true        # ← suprime erros de TUDO que não está na lista abaixo
[[tool.mypy.overrides]]
module = ["domain","domain.*","application","application.*","core","core.*"]
ignore_errors = false
```
Por isso `mypy pipeline` diz "Success" **mesmo com erros** — eles estão sendo IGNORADOS. Para verificar de verdade, use um config estrito temporário:
```
# /tmp/strict-mypy.ini
[mypy]
python_version = 3.12
ignore_missing_imports = True
explicit_package_bases = True
ignore_errors = False
```
`.venv/bin/mypy --config-file /tmp/strict-mypy.ini <pacote>` → estado REAL.

## Tarefa (escopo FECHADO — interfaces/auth/validation/app ficam para follow-up)
1. **Corrigir os 3 erros de rag** (sem mudar comportamento):
   - `rag/enrichment_node.py:52` `"object" has no attribute "search_similar_incidents"`: o parâmetro `knowledge_retriever: object` deve ser tipado `KnowledgeRetriever` (import em `TYPE_CHECKING` de `rag.knowledge_retriever`).
   - `rag/reranker.py:293/296` `"None" has no attribute "predict"`: `self._cross_encoder` é `None` até carregar; tipe `_Predictor | None` (Protocol mínimo `def predict(self, pairs: list[Any]) -> Any`) e use `cast(_Predictor, self._cross_encoder)` nos pontos de uso (ou guard `if self._cross_encoder is None`). NÃO mude a lógica de carregamento/uso.
   - Confirme `.venv/bin/mypy --config-file /tmp/strict-mypy.ini rag` → Success.
2. **Estender o gate** — edite `pyproject.toml`: adicione à lista `ignore_errors = false` os pacotes agora limpos: `pipeline`, `pipeline.*`, `safety`, `safety.*`, `data`, `data.*`, `agents`, `agents.*`, `tools`, `tools.*`, `rag`, `rag.*`. **MANTENHA** `module = "*" ignore_errors = true` para o resto (interfaces/auth/validation/app) — isso suprime os erros dos módulos transitivos ainda não limpos quando importados pelos pacotes do gate.
3. **Atualizar o comando do gate**: `scripts/ci-local.sh:79` e `.github/workflows/ci.yml:47` de `mypy domain application core` para `mypy domain application core pipeline safety data agents tools rag`.
4. **Verificar o gate estendido**: `.venv/bin/mypy domain application core pipeline safety data agents tools rag` (com o pyproject já editado) → **Success**. Se algum erro aparecer em módulo transitivo (auth/interfaces), ele deve estar suprimido pelo override `"*"`; se NÃO estiver, reporte — não tente limpar o closure inteiro nesta fase.

## Regras INVIOLÁVEIS
- ZERO mudança de comportamento. A suíte unit deve continuar **2169 passed** (ou mais), 0 failed.
- Sem `# type: ignore` genérico nem `Any` indiscriminado; `cast`/Protocol/guards `None`/`TYPE_CHECKING` são ok.
- NÃO regen snapshots (se algum mudar, PARE e reporte).
- Sem deps novas. Nada gravar em `output/` (tmp_path). Confirme file:line.
- NÃO toque interfaces/auth/validation/app nesta fase (follow-up separado).

## Verificação LOCAL M5 (antes do commit)
- `.venv/bin/python -m pytest tests -m unit -q` → 2169 passed, 0 failed.
- `.venv/bin/python -m pytest tests -m snapshot -q` → 17 passed, snapshots INALTERADOS.
- `.venv/bin/mypy domain application core pipeline safety data agents tools rag` → Success.
- `.venv/bin/ruff check .` → All checks passed.
- `.venv/bin/python main.py --no-llm --no-rag --output-dir /tmp/gate-smoke; echo exit=$?` → 3.
- NÃO use `scripts/ci-local.sh unit` nem M1.

Ao terminar: commit incluindo `AIOPS-98` (ex: `chore(higiene): estende gate mypy a pipeline/safety/data/agents/tools/rag + corrige rag (AIOPS-98)`). Setar `feature_list.json[0].passes=true` SOMENTE com o gate estendido verde completo. Reporte: rag corrigido, override final, comando do gate, números da verificação.