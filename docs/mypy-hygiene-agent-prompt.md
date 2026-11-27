# AIOPS-98 — Higiene de tipos: zerar dívida mypy fora do gate + estender o gate

Worker Claude: implemente no repo `/Users/allanbrunom5/Projetos/pessoais/aiops-agent` (branch main). Leia PRIMEIRO `claude-progress.txt`, `pyproject.toml` ([tool.mypy]), `scripts/ci-local.sh:79`, `.github/workflows/ci.yml:47`, e `git log --oneline -6`.

## Objetivo
O gate mypy atual cobre só `domain application core` (limpo, 22 arquivos). Os módulos fora do gate acumulam dívida de tipos que o pi-lens reporta como blockers a cada edit. **Zere os erros mypy fora do gate SEM mudar comportamento**, depois **estenda o gate** para cobri-los.

## Inventário (verifique com mypy por pacote; rode `.venv/bin/mypy <dir>` separadamente)
- `pipeline/`: 9 erros — `context_compressor.py` (4), `trace_filter.py` (1), `log_parser.py` (1), `aggregator.py` (1), + `data/sample_incident.py` (2 via imports).
- `safety/`: 12 erros em 3 arquivos — incl. `tnr.py` usar `callable` como tipo (devia `typing.Callable`) + guards `Optional`/`ModuleType|None`.
- `agents/`: 23 erros em 6 arquivos.
- `tools/`: 6 erros em 2 arquivos.
- `data/`: 2 erros em 1 arquivo.
- `interfaces/`: 1 erro (atenção: rodar `mypy interfaces` pode dar "Source file found twice under different module names cli.X vs interfaces.cli.X" — conflito de pacote; resolva com `explicit_package_bases`/`mypy_path` no `[tool.mypy]` ou rode por pacote correto).
- Testes com erro de tipo Pyright: `tests/test_cli_safety_decision.py:65` passa `dict[str,bool]` onde se espera `SafetyConfig` — corrija o teste para construir `SafetyConfig` de verdade (sem mudar o que o teste verifica).

## Regras INVIOLÁVEIS
- **ZERO mudança de comportamento.** Só anotações de tipo, guards `None`, `typing.Callable`/`Optional`/`cast`, `TYPE_CHECKING`, stubs `.pyi` se necessário. A suíte de testes deve continuar **idêntica em resultado** (2169 passed antes; não pode cair).
- **Não** silencie erros com `# type: ignore` genérico nem `Any` indiscriminado. Se um ignore for inevitável, use código de erro específico (`# type: ignore[code]`) + comentário justificando, e conte-os no relatório.
- **Não** regen snapshots (mudança de tipo não altera saída). Se algum snapshot mudar, PARE e reporte — indica mudança de comportamento proibida.
- Sem deps novas. Nada gravar em `output/` (tmp_path). Confirme `file:line` antes de editar.
- TDD quando aplicável: se corrigir um bug real de tipo que tinha comportamento errado, teste falha antes.

## Escopo / ordem (commits incrementais — pode parar e reportar progresso)
1. `pipeline/` + `data/` (os blockers recorrentes do pi-lens) + `tests/test_cli_safety_decision.py`.
2. `safety/` (incl. `tnr.py` callable→Callable).
3. `agents/` + `tools/` + `interfaces/`.
4. **Estender o gate**: `scripts/ci-local.sh:79` e `.github/workflows/ci.yml:47` de `mypy domain application core` para incluir os pacotes agora limpos (ex: `mypy domain application core pipeline safety agents tools data interfaces`). Só estenda depois de TODOS limpos. Configure `[tool.mypy]` (explicit_package_bases/mypy_path) se preciso p/ evitar o conflito de nomes de módulo.

## Verificação LOCAL M5 (obrigatória antes de CADA commit)
- `.venv/bin/python -m pytest tests -m unit -q` → **2169 passed** (ou mais), 0 failed.
- `.venv/bin/python -m pytest tests -m snapshot -q` → 17 passed, snapshots INALTERADOS.
- `.venv/bin/mypy <pacotes corrigidos>` → Success.
- `.venv/bin/ruff check .` → All checks passed.
- `.venv/bin/python main.py --no-llm --no-rag --output-dir /tmp/hygiene-smoke; echo exit=$?` → 3.
- NÃO use `scripts/ci-local.sh unit` nem M1.

Ao terminar (ou ao parar com progresso parcial): commit com mensagem incluindo `AIOPS-98` / `higiene mypy` (ex: `chore(higiene): mypy limpo em pipeline+safety+data, gate estendido (AIOPS-98)`). Setar `feature_list.json[0].passes=true` SOMENTE quando o gate estendido estiver verde por completo; se parcial, deixe passes=false e reporte o que falta. Reporte: erros corrigidos por pacote, ignores específicos usados (se houver), gate final.