# F5-4 — Spec do épico: paridade V1 → aposentar o V0

> Implementador: você é um engenheiro sênior portandoo V0 para o V1 até paridade, para
> então aposentar o V0. **Fonte autoritativa de wiring: `docs/f5-4-parity-map.md`** (leia
> PRIMEIRO, inteiro). Este spec define as tarefas; o mapa tem os file:line exatos.
> **Confirme cada file:line antes de editar** — o código pode ter mudado.

## Regras inegociáveis

1. **Não quebre o V0** enquanto o V1 não tiver paridade. O V0 continua o engine default
   até P6. Nada de remover `interfaces/cli/v0_pipeline.py`, a flag `--pipeline-v1`, ou o
   bloco de compat de `main.py` antes de P6 (gated).
2. **Exit codes JÁ estão em paridade** (V0=3, V1=3 com `--no-llm --no-rag`). NÃO replanejar
   exit codes (4a done). Preserve o contrato público: 0 resolvido / 1 erro / 2 regressão / 3 aprovação humana.
3. **Teste falha antes do fix.** Escreva o teste que reproduz o gap, veja falhar, implemente, veja passar.
4. **Write-set explícito por tarefa.** Só toque os arquivos listados. Mudança fora do write-set = pare e justifique.
5. **Verificação LOCAL no M5** (NÃO ssh/rsync m1-docker; notas antigas "no M1" são erro corrigido):
   - `.venv/bin/python -m pytest tests -m unit -q` (suíte completa, leve, roda aqui).
   - snapshots: `.venv/bin/python -m pytest tests/test_snapshots.py tests/test_memory_leak_snapshots.py tests/test_unknown_fault.py -q`.
   - smoke: `.venv/bin/python main.py --no-llm --no-rag --output-dir /tmp/v0-smoke; echo $?` e com `--pipeline-v1`.
   - `.venv/bin/ruff check .` e mypy no gate (domain application core).
   - **NÃO use `scripts/ci-local.sh unit`** (cria venv fresco = pesado, hook bloqueia no M5). Use `.venv/bin/python -m pytest` direto.
6. **Nada de gravar em `output/` do repo** — `tests/conftest.py:201-215` `_forbid_writes_to_repo_output` falha testes que escrevem lá. Use `tmp_path` / `/tmp`.
7. **TNR é SIMULADO** — nunca executa infra real (SECURITY.md). Propõe + aprovação humana, exit 3.
8. **Sem deps novas.** Stdlib + o que já está no projeto.
9. **Marque `passes:true`** no feature_list.json após suíte verde + commit. Commit message inclua o id da tarefa (P1/P2/...). Uma sessão por tarefa basta.
10. **Snapshots**: só regenere em P5, com diff justificado por escrito. Antes disso, NÃO rode `--snapshot-update`.

## Tarefas (ordem de dependência)

### P1 — Adapter state→result + captura estruturada (fundação)
- **Objetivo**: criar o adapter que converte o `IncidentState` do V1 no `result` dict que
  `render_postmortem_markdown`/`serialize_result` esperam, e fazer os safety nodes guardarem
  os dicts estruturados (hoje só guardam verdict/reasons).
- **Mapa**: §1 (dados necessários por seção) + §6 P1.
- **Write-set**: `blueprint/adapters/state_adapter.py` (novo, `state_to_result(state)->dict`),
  `blueprint/adapters/safety_nodes.py` (guardar safety_validation dict :189→:213-221 e remediation dict :109→:142),
  `blueprint/adapters/pipeline_nodes.py` (guardar stats/san_report se necessário),
  `domain/incident_state.py` (campo timeline OU convenção `metadata["_timeline"]`),
  `tests/test_state_adapter.py` (novo).
- **Aceite**: testes unit do adapter cobrindo as 6 seções (root_cause, timeline, mitigation.action+pattern_matched,
  safety_validation, remediation_validation, verification); sem mudança de comportamento do V1 (unit green);
  o adapter reconstrói MitigationAction a partir do dict proposed_action (ver safety_nodes.py:180).
- ** deps**: none.

### P2 (4b) — Postmortem rico no V1
- **Objetivo**: `make_postmortem_node` (blueprint/adapters/pipeline_nodes.py:240) passa a chamar
  `render_postmortem_markdown` via o adapter P1, emitindo as 12 seções. Manter o incident em
  `metadata["_incident"]` (create_incident_state, v1_runner.py:75). Registrar timeline nas transições.
  No-LLM: TNR via `mitigator.verify_with_tnr` (como V0 :594) ou not_verified honesto.
- **Mapa**: §1 inteiro.
- **Write-set**: `blueprint/adapters/pipeline_nodes.py` (make_postmortem_node),
  `interfaces/cli/v1_runner.py` (create_incident_state guarda incident; wiring),
  `blueprint/adapters/safety_nodes.py` (tnr_node no-LLM path se necessário),
  `tests/test_v1_postmortem_parity.py` (novo).
- **Aceite**: `main.py --pipeline-v1 --no-llm --no-rag --output-dir /tmp/x` gera postmortem com as 12 seções
  (Summary, Root Cause, Evidence, Timeline+State Machine Transitions, Mitigation Proposed, Pre-Execution Safety Check,
  Remediation CVE/Version Validation, TNR Verification, Action Items, Lessons Learned, [Incident Tracking condicional]);
  diff vs V0 justificado por escrito no commit. Exit code ainda 3. Unit green. NÃO regenere snapshots (P5).
- **deps**: P1.

### P3 (4c) — Flags no run_v1
- **Objetivo**: run_v1 honra --output-format (json/both via serialize_result/step_postmortem),
  --tracker (_resolve_tracker + step_tracking + _attach_postmortem_to_tracker), --data-source (databricks),
  --rebuild-rag. Fix mkdir parents=True (display.py:302).
- **Mapa**: §2.
- **Write-set**: `interfaces/cli/v1_runner.py`, `interfaces/cli/postmortem.py` (se precisar),
  `display.py` (mkdir parents), `tests/test_v1_flags.py` (novo), `tests/test_args.py` (se precisar).
- **Aceite**: smoke de cada flag no V1; --output-format json gera .json; exit codes inalterados; unit green.
- **deps**: P1, P2.

### P4a (4d) — Node preditivo + Slack progressivo
- **Objetivo**: V1 ganha node preditivo (run_predictive_stage, stages.prediction) + eventos Slack
  diagnosis.completed e prediction.warning/alert (hoje V1 só 3 eventos, V0 tem 5).
- **Mapa**: §3 (preditivo, Slack).
- **Write-set**: `blueprint/adapters/pipeline_nodes.py` (node preditivo), `app/builders.py` (build_pipeline),
  `interfaces/cli/v1_runner.py` (slack events), `tests/test_v1_predictive.py` (novo).
- **Aceite**: V1 stages.prediction populated; 5 tipos de evento Slack; unit green; exit codes inalterados.
- **deps**: P1.

### P4b (4d) — Memória episódica + skill learning
- **Objetivo**: V1 persiste IncidentRecord (IncidentMemoryDB().save_incident) + skill read/route
  (find_similar_incidents, SkillRouter) + write/evolve (SkillOutcome, SkillEvolver agents/skill_evolver.py:46;
  update_skill_score EMA só quando use_llm).
- **Mapa**: §3 (memória, skills) + v0 step_persist_incident:1025, step_agents:419, step_skill_learning:712.
- **Write-set**: `blueprint/adapters/pipeline_nodes.py`, `app/builders.py`, `interfaces/cli/v1_runner.py`,
  `tests/test_v1_memory_skills.py` (novo).
- **Aceite**: V1 persiste incident + rota/evolui skills como V0; unit green; best-effort não quebra exit codes.
- **deps**: P1.

### P4c (4d) — Cache FAISS disco + tabela-resumo
- **Objetivo**: V1 RAG com cache disco (.aiops-cache/rag, content_hash, load_index/save_index, rebuild flag)
  + print_summary (tabela-resumo display.py:62) no V1.
- **Mapa**: §3 (cache FAISS, tabela) + v0 step_rag:313.
- **Write-set**: `interfaces/cli/v1_runner.py`, `app/builders.py`, `display.py`, `rag/` (se precisar),
  `tests/test_v1_cache_summary.py` (novo).
- **Aceite**: V1 usa cache disco + imprime tabela-resumo; unit green; exit codes inalterados.
- **deps**: P3.

### P5 (4e) — conftest → V1 + regen snapshots [GATED — coordenador verifica paridade antes]
- **Objetivo**: `tests/conftest.py:243-383` `_run_pipeline` passa a buildar engine+pipeline V1 via app.builders
  (no_llm/no_rag), adaptar state→result (adapter P1); regen 12 snapshots com diff justificado; migrar
  test_exit_codes.py p/ V1 (patch namespace certo).
- **Mapa**: §4 inteiro. **RISKIEST.**
- **Write-set**: tests/conftest.py, tests/test_exit_codes.py, tests/snapshots/* (regen), 9 arquivos que patcham v0_pipeline,
  tests/test_snapshots.py:122, tests/test_unknown_fault.py.
- **Aceite**: snapshots green com diffs justificados por escrito; test_exit_codes testa V1 default; unit+snapshot green.
- **deps**: P1, P2, P3, P4a, P4b, P4c. **Só despachar após coordenador confirmar paridade V1↔V0.**

### P6 (4f) — Remoção do V0 + docs [GATED — coordenador verifica + autorização]
- **Objetivo**: remover interfaces/cli/v0_pipeline.py (mover _extract_fault_tags/_record_safety_decision p/ application/
  se V1 precisar), bloco compat main.py:31-107 + dispatch→run_v1 default, flag --pipeline-v1 (args.py:51-53),
  _validate_monitor_args:163; atualizar README/docs (§5). Manter EXIT_* re-export de main (ou migrar testes).
- **Mapa**: §5 inteiro. **RISKIEST (contrato público).**
- **Write-set**: interfaces/cli/v0_pipeline.py (delete), main.py, interfaces/cli/args.py, display.py, README.md,
  README.pt-BR.md, docs/architecture.md, docs/api-contracts-internal.md, docs/improvement-progress.md, testes migrados.
- **Aceite**: V1 é o único engine; unit+snapshot+integration green; smokes exit 3; contrato público de exit codes preservado;
  ruff/mypy clean; docs sem menção a V0/--pipeline-v1 como opção.
- **deps**: P5. **Só despachar após coordenador verificar equivalência + autorização explícita.**

## Verificação final do épico (coordenador)
- unit + snapshot + integration green; smokes V0/V1 (e pós-remoção só V1) exit 3.
- Postmortem V1 ≡ V0 (12 seções, diffs justificados); flags honradas; features presentes.
- pi-lens 0 blockers nos arquivos tocados; ruff/mypy gate clean.