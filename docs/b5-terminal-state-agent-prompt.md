# B5 — Estado terminal honesto + mapa completo de exit codes (AIOPS-76)

Worker Claude: implemente esta tarefa no repo `/Users/allanbrunom5/Projetos/pessoais/aiops-agent` (branch main). Leia PRIMEIRO `ROBUSTNESS_PLAN.md` seção **B5** (linha 211) e **C1** (251), `claude-progress.txt`, e `git log --oneline -6`.

## Contexto importante (V0 foi aposentado)
O plano B5 original referencia `interfaces/cli/v0_pipeline.py` — **esse arquivo NÃO existe mais** (épico F5-4 P6 removeu o V0; V1 Blueprint Engine é o único pipeline). C1/C2 (decisão agregada + orchestrator fail-closed) **já está DONE** (AIOPS-67). Re-mapeie B5 para o V1. Parte de B5 **já foi herdada** pelo V1 — NÃO refaça o que já está correto; confirme e complete os gaps reais abaixo.

## O que JÁ está correto no V1 (confirme, não quebre)
- `blueprint/adapters/pipeline_nodes.py:426`: `"success": tnr_result == TNRResult.SUCCESS` — success só com TNR medido SUCCESS.
- `interfaces/cli/v1_runner.py:170-213`: exit map já faz `success→0`, `tnr in (regression, rollback_complete, error)→2`, `requires_human→3`, senão `1`.

## Gaps REAIS a implementar (write-set)
1. **Estado terminal honesto** — `domain/incident_state.py:43` (`state: str`, labels `DETECTING|DIAGNOSING|MITIGATING|VERIFYING|RESOLVED`). Hoje não há rótulo `AWAITING_APPROVAL`. Localize onde o V1 seta `state="RESOLVED"` (grep `RESOLVED` em `blueprint/`, `domain/`, `application/`). Garanta: `RESOLVED` **somente** quando (a) TNR **medido** success, ou (b) ação read-only auto-aprovada por política explícita (`require_human_approval=false` + LOW + allowlist). Caso contrário (NOT_VERIFIED / ação pendente / requer humano) → estado terminal `AWAITING_APPROVAL` (novo rótulo) com `success=False` e exit 3. **Nunca** `RESOLVED`+`success=True` com TNR não-verificado e ação proposta pendente. Adicione `AWAITING_APPROVAL` aos labels/docstring.
2. **Skill learning só com sucesso MEDIDO** — `blueprint/adapters/pipeline_nodes.py:453` `tnr_passed = tnr_result == "success"` alimenta `memory_db.update_skill_score(skill_id, tnr_passed, ...)` (:468). O outcome do orchestrator (`agents/orchestrator.py:353`) usa TNR **simulado** (`tnr_verification_mode="simulated"`, F2-3). Gate: `tnr_passed` para aprendizado deve ser True **só** quando `tnr_result=="success"` **E** `verification_mode=="measured"` (o outcome carrega `tnr_verification_mode`). Simulado não pode inflar skill score como se fosse medido. Preserve o registro do modo.
3. **Mapa completo de exit codes documentado** — `interfaces/cli/exit_codes.py` já tem `EXIT_CODE_LABELS`. Documente a matriz completa (0/1/2/3, precedência regressão>aprovação, e o que cada valor de TNR produz) no `README.md` (seção exit codes existente) e/ou `docs/exit-codes.md`. Inclua o estado terminal honesto (RESOLVED vs AWAITING_APPROVAL).
4. **Testes matriz TNR×exit-code** — estenda `tests/test_exit_codes.py` (já cobre 0/1/2/3 + precedência). Para CADA valor de `TNRResult` (`safety/tnr.py:24-31`: success/not_verified/regression/rollback_complete/error/skipped), assert do par (estado terminal, exit code) esperado. Inclua: NOT_VERIFIED+ação pendente → AWAITING_APPROVAL+exit3 (nunca 0); rollback_complete/error → exit2; regressão tem precedência sobre aprovação; skill score não sobe com simulated.

## Critérios de aceite
- Matriz TNR×exit-code coberta por testes (todos os valores de TNRResult).
- Nenhum caminho produz exit 0 + `success=True` + `RESOLVED` com TNR não-verificado e ação pendente.
- Regressão continua tendo precedência sobre aprovação.
- Skill learning não recompensa sucesso simulado como medido.
- Mapa de exit codes documentado.

## Regras
- Teste falha antes do fix (TDD) quando aplicável.
- Nada gravar em `output/` do repo (use `tmp_path`).
- TNR simulado nunca executa infra real (SECURITY.md).
- Sem deps novas. Confirme `file:line` antes de editar (o plano referencia V0 removido — re-mapeie).
- NÃO quebrar contrato CLI: exit 0/1/2/3, `--no-llm/--no-rag/--output-dir/--output-format/--monitor/--source/--interval`.
- Não regen snapshots sem justificar (mudança de estado terminal pode afetar `tests/snapshots/` — se afetar, documente a mudança consciente no commit).

## Verificação LOCAL M5 (obrigatória, tudo verde antes de commit)
- `.venv/bin/python -m pytest tests -m unit -q` (TUDO verde)
- `.venv/bin/python -m pytest tests -m snapshot -q` (verde)
- `.venv/bin/python main.py --no-llm --no-rag --output-dir /tmp/b5-smoke; echo exit=$?` (deve ser 3)
- `.venv/bin/ruff check .`
- `.venv/bin/mypy domain application core`
- NÃO use `scripts/ci-local.sh unit` nem M1.

Ao terminar: commit com mensagem incluindo `B5` (ex: `feat(robustez): B5 estado terminal honesto + matriz exit codes + skill learning medido`). Depois setar `feature_list.json[0].passes=true` (python json). Reportar resumo + números da verificação.