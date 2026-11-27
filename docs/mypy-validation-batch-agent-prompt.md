# AIOPS-99 lote 4 — Higiene mypy: validation (root fix ScenarioFn + residuais)

Worker Claude: repo `/Users/allanbrunom5/Projetos/pessoais/aiops-agent` (branch main). Leia PRIMEIRO `claude-progress.txt`, `pyproject.toml` ([tool.mypy]), `scripts/ci-local.sh:79`, `.github/workflows/ci.yml:47`, `data/registry.py`, `git log --oneline -5`.

## ESCOPO: `validation/` + a correção RAIZ em `data/registry.py` (ScenarioFn). NÃO toque interfaces/, app/, auth/, nem outros pacotes. Over-scoping = FALHA.
Lotes 1-3 feitos (auth d698b3f, interfaces/http 980621c, interfaces/mcp+cli fac2322). Gate atual = `domain application core pipeline safety data agents tools rag auth interfaces/http interfaces/mcp interfaces/cli` (132 files Success). Override `module="*" ignore_errors=true` suprime transitivos.

## A CAUSA RAIZ (LEIA — resolve ~289 dos 330 erros de uma vez)
`data/registry.py:32`: `ScenarioFn = Callable[[int], dict[str, Any]]`. Isso exige `seed` POSICIONAL obrigatório. Mas as funções de cenário reais têm `seed: int = 42` (default → opcional, aceito como kwarg). Por isso mypy acusa `get_scenario(name)(seed=42)` e `get_scenario(name)()` de "Missing positional argument 'seed'" em ~289 lugares (validation + conftest + testes).
**Correção RAIZ** (type-only, behavior-preserving — o runtime já aceita seed kwarg/opcional): troque o alias por um Protocol com seed opcional:
```python
from typing import Protocol
class ScenarioFn(Protocol):
    def __call__(self, seed: int = ...) -> dict[str, Any]: ...
```
Mantenha `get_scenario(name: str) -> ScenarioFn` e `register_scenario(name: str, fn: ScenarioFn)`. Confirme que `data/` continua strict-clean após a mudança: `.venv/bin/mypy --config-file /tmp/strict-mypy.ini data` → Success.
**Depois de corrigir a raiz, RE-MEÇA**: `.venv/bin/mypy --config-file /tmp/strict-mypy.ini validation` — os ~289 call-arg devem sumir, restando ~41 erros reais.

## Os ~41 erros residuais de validation (corrija SEM mudar comportamento)
Categorias (confirme file:line atual após o root fix):
- **`object` has no attribute `value`/`get`** (rag_retrieval.py:343/363/369/394, cross_service_cascade.py:96, latency_spike.py:52, resource_saturation.py:49, knowledge_write_read.py, etc.): valores de dict tipados `object`. **Correção**: tipe os dicts/valores com o tipo real (ex.: `dict[str, Any]` onde é heterogeneous, ou narrow com isinstance/cast no ponto de uso `.value`/`.get`). Leia a origem do dado. NÃO use Any indiscriminado se um tipo preciso serve; `cast` pontual ok.
- **`str + int`** (validation/report.py:62): concatenação/soma com tipos mistos. **Correção**: leia o contexto; provavelmente formatação de string — use f-string/str() coerente SEM mudar o output.
- Outros arg-type/assignment residuais: narrow/cast conforme o tipo real.

## Estender o gate (só depois de validation strict-clean)
- `pyproject.toml`: adicione `"validation", "validation.*"` à lista `ignore_errors = false`. **MANTENHA** `module="*" ignore_errors=true` (app/, interfaces/demo ainda suprimidos).
- `scripts/ci-local.sh:79` e `.github/workflows/ci.yml:47`: comando → `mypy domain application core pipeline safety data agents tools rag auth interfaces/http interfaces/mcp interfaces/cli validation`.
- Verifique esse comando → **Success**.
- Confirme validation strict-clean: `.venv/bin/mypy --config-file /tmp/strict-mypy.ini validation` → Success.

## Regras INVIOLÁVEIS
- ZERO mudança de comportamento. Suíte unit **2169 passed**, 0 failed. O root fix ScenarioFn é type-only.
- Sem `# type: ignore` genérico; `cast`/Protocol/isinstance/TYPE_CHECKING ok. `dict[str, Any]` ok onde o dado é genuinamente heterogêneo.
- NÃO regen snapshots (se algum mudar, PARE e reporte).
- Sem deps novas. Nada gravar em `output/` (tmp_path). Confirme file:line.
- data/ deve permanecer strict-clean. NÃO toque interfaces/app/auth.

## Verificação LOCAL M5 (antes do commit)
- `.venv/bin/mypy --config-file /tmp/strict-mypy.ini data validation` → Success.
- gate estendido Success.
- `.venv/bin/python -m pytest tests -m unit -q` → 2169 passed, 0 failed.
- `.venv/bin/python -m pytest tests -m snapshot -q` → 17 passed, INALTERADOS.
- `.venv/bin/ruff check .` → All checks passed.
- `.venv/bin/python main.py --no-llm --no-rag --output-dir /tmp/val-smoke; echo exit=$?` → 3.
- NÃO use `scripts/ci-local.sh unit` nem M1.

Ao terminar: commit incluindo `AIOPS-99` (ex: `chore(higiene): ScenarioFn Protocol + mypy limpo em validation + gate estendido (AIOPS-99 lote 4)`). Setar `feature_list.json[0].passes=true` SOMENTE com validation strict-clean + gate verde. Reporte: root fix, nº de cascades resolvidos, residuais corrigidos, números.