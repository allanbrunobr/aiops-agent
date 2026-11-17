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

---

## Fase 2 — V1 honesto + segurança real — DONE (2026-11-17)

### F2-1 Wire de state.proposed_action — done
- `make_diagnoser_node` extrai a ação de `outcome.mitigation` e seta
  `state.proposed_action`; `make_diagnoser_fallback` (modo --no-llm) também
  propõe via `MitigationAgent().propose(diagnosis)` — sem isso as safety
  layers skipavam no modo padrão da demo.
- Conversão na borda do blueprint (`_proposed_action_from_mitigation`):
  dataclass MitigationAction (domain, tipo ÚNICO — alegação de "dois tipos"
  da auditoria estava desatualizada) → dict {description, command, risk_level,
  rollback}, o contrato que os safety nodes já consumiam.
- Testes: `tests/test_v1_safety_wiring.py` (3): fallback seta ação; pipeline
  completo sem skips (safety_reasons com VersionValidator+PreValidator);
  E2E ação perigosa injetada → PipelineAborted em version/pre_validator.
  Os 2 primeiros falhavam antes do fix.

### F2-2 RAG nasce vazio no V1 — done
- `_ensure_rag_index(state, report_obj)` chamado nos dois diagnoser nodes:
  build lazy do índice a partir do report agregado quando o retriever está
  anexado e `no_rag=False`. Falha de indexação degrada graciosamente com
  warning estruturado (RAG não é camada de safety).
- Testes: `tests/test_v1_rag_index.py` (2): índice construído + search retorna
  chunks do incidente corrente (faiss/model mockados); no_rag=True não indexa.
- Verificação end-to-end: o primeiro smoke V1 com RAG saiu 0, mas por
  DEGRADAÇÃO GRACIOSA — o .venv local (Python 3.13) não tinha faiss/torch/
  sentence-transformers (requirements.txt pin torch<2.5, sem wheel cp313),
  e o build lazy logou `rag.index_build_failed` sem quebrar o pipeline.
  **Correção de ambiente (aprovada pelo usuário):** .venv recriado com
  Python 3.12 + requirements.txt completo + dev group; smoke re-verificado
  com índice REAL construído (ver Fase 3, nota de ambiente).

### F2-3 TNR de teatro → simulação declarada com dados reais — done
- **PRÉ** derivado da telemetria: `health_snapshot_from_report(report)` em
  agents/mitigator.py (error_rate da maior anomalia 'error'; latency do maior
  slow trace; availability=100−error; pool de anomalias 'pool/connection').
  Proxies documentados no docstring.
- **PÓS** é projeção determinística DECLARADA: `simulate_action_effect(action,
  pre)` em safety/tnr.py — destrutivos (delete/rm/drop/truncate/replicas=0)
  degradam; rollback/undo/restart/scale-up recuperam; demais inalterado.
  Nenhuma ação é executada em infra real (SECURITY.md) — o pós nunca é medição.
- `TNRReport.verification_mode` ("simulated"/"real") distinguishable no
  reporter (TNR_VERIFIED), audit (metadata) e JSON de verificação do V0;
  `execute_with_safety` (design real, sem callers) marca "real".
- Rename `verify_with_tnr` → `simulate_tnr_verification(mitigation, guard,
  report)`; callers atualizados (orchestrator ×2, main.py V0, conftest,
  test_smoke, test_reporter, mocks do test_orchestrator).
- Orchestrator: acoplamento `getattr(getattr(...))` substituído por acesso
  tipado (`tnr_result.result == TNRResult.SUCCESS`); AgentOutcome ganhou
  `tnr_verification_mode`.
- SafetyOrchestrator: baseline idêntico pré/pós eliminado — pré de
  `system_state["pre_health"]` quando disponível, senão nominal documentado;
  pós via projeção → ações destrutivas agora REGRIDEM e bloqueiam.
- MCP `_check_tnr` (código morto com snapshots idênticos) REMOVIDO.
- `current=3/prev=1` agora vêm do diagnóstico (`current_replicas`/
  `previous_revision`) com defaults documentados como premissas PoC.
- Testes: `tests/test_tnr_simulation.py` (6): pré derivado do report;
  destrutiva → REGRESSION; rollback → SUCCESS; SafetyOrchestrator pré≠pós;
  destrutiva bloqueada por TNR (approved=False); projeção determinística.
  Replan por regressão exercitado no test_orchestrator atualizado (mocks
  tipados com TNRResult).
- **Diffs de snapshot (3, justificados — regra 8):** `full_pipeline_result`,
  `postmortem_structure`, `unknown_fault_postmortem_structure` — a seção
  verification mudou de saúde pré FABRICADA (19%, err=45/avail=55) para
  saúde pré DERIVADA DA TELEMETRIA REAL (36%), pós simulado (96%), delta
  +77.9→+59.3, e novo campo `verification_mode: "simulated"`. Resultado
  continua "success"; estrutura e contrato de exit codes inalterados.
- README.md + README.pt-BR.md: divulgada a natureza simulada do TNR.

### F2-4 Fail-open → fail-closed — done
- SafetyOrchestrator: exceção no sanitizer ou TNR → `passed=False` +
  `blocked_by` (antes: `passed=True, skipped=True`).
- MCP `_sanitize_result`: exceção → campo recebe "[REDACTED: sanitization
  unavailable]" — nunca o texto original (PII vazava no erro).
- pre_validator: falha de auditoria (inclusive do log_error) → log.error
  estruturado `safety.pre_validator.audit_failed` (logger novo no módulo);
  a decisão de validação não muda por falha de auditoria.
- Testes: `tests/test_safety_fail_closed.py` (4), todos falhavam antes do fix.
- Aceite: `rg '"passed": True' safety/` → zero ocorrências.

### F2-5 Bypass de allowlist por substring — done
- `_check_command_safety` reescrito: (1) rejeita operadores de composição
  (&&, ||, ;, |, backtick, $(), newline) ANTES de qualquer allowlist;
  (2) tokeniza com shlex (aspas malformadas → bloqueio); (3) blocklist;
  (4) HIGH_RISK/SAFE/ROLLOUT com `re.match` ANCORADO nos tokens normalizados
  (binário+subcomando reais); (5) default-deny preservado.
- Testes: `tests/test_allowlist_bypass.py` (19): 8 compostos bloqueados
  (6 falhavam antes do fix), 8 read-only simples passam, rollout reversível,
  destrutivos (rm -fr /, find -delete, delete namespace) bloqueados,
  desconhecidos em default-deny.

### Verificação da fase
- ruff ✓, mypy ✓, unit: **749 passed**; integration/snapshot: **138 passed**
- Smokes: V0 exit 3, V1 exit 0; V1 com RAG real exit 0.
- CI completo (`scripts/ci-local.sh`): registrado no commit da fase.

### Testes adicionados na fase
- 34 testes (3 F2-1 + 2 F2-2 + 6 F2-3 + 4 F2-4 + 19 F2-5).

---

## Fase 3 — MCP/HTTP/demo/webhook funcionais — DONE (2026-11-17)

### F3-1 MCP diagnose_incident quebrado — done
- `create_app`: engine/retriever construídos UMA vez (composition root no topo)
  e passados ao `mount_sse` (engine=, retriever=) — antes só existiam em
  app.state para o A2A; o MCP nascia sem eles (engine_unavailable).
- `handle_diagnose_incident`: monta IncidentState a partir do NormalizedAlert
  e chama `engine.run(pipeline, state)` (assinatura real — antes
  `engine.run(alert)` → TypeError mascarado por except). Extrai campos do
  IncidentState (diagnosis/evidence/proposed_action/safety_verdict) em vez de
  atributos inexistentes; confiança textual→numérica via _CONFIDENCE_SCORES.
- **Decisão registrada (regra 9):** o alerta MCP não traz telemetria; o PoC
  usa o sample simulado como fonte, em modo determinístico (no_llm/no_rag),
  com `"telemetry_source": "simulated-sample"` explícito na resposta.
- `PipelineAborted` → resposta estruturada `safety_blocked` (403), não 500.
- Testes: `tests/test_diagnose_incident.py` reescrito com o contrato REAL
  (mocks antigos mascaravam o bug): 8 unit + 1 integration com engine real.

### F3-2 A2A engine.run bloqueia event loop — done
- `await asyncio.to_thread(engine.run, pipeline, state)` em `_handle_diagnose`.
- Teste: `tests/test_a2a_concurrency.py` — ticker assíncrono progride DURANTE
  run bloqueante de 0.5s (falhava antes do fix: loop preso).

### F3-3 Webhook → pipeline: IMPLEMENTADO (não rotulado) — done
- **Decisão registrada:** implementar de verdade (preferência do plano).
  `PipelineRunner` ganha engine/config/notifier; `_execute_pipeline` executa
  o BlueprintEngine V1 real em thread, persiste estado final no store,
  gera postmortem e emite eventos LifecycleEvent (pipeline.started/completed/
  failed) ao notifier Slack. Stub removido.
- Telemetria: sample simulado sinalizado (`telemetry_source` no metadata/log)
  — webhook traz metadados do alerta, não séries.
- `infra/run_webhook_server.py`: wiring completo — InMemoryStore +
  build_engine(config, store) + SlackNotifier **dry-run por padrão**; envio
  real só com SLACK_BOT_TOKEN E AIOPS_WEBHOOK_SLACK_REAL=1.
- Fixture criado: `infra/grafana_webhook_payload_example.json` (o diretório
  docker/ virou infra/; o JSON não existia em lugar nenhum).
- `tests/integration/test_webhook_to_slack_flow.py`: caminho corrigido,
  classe TestWebhookPayloadValidation UN-skipped (3 testes rodando), asserts
  200→202 (semântica async real do endpoint); classe live-server segue skip
  (requer Docker) com reason atualizado.
- Teste novo: `tests/integration/test_webhook_pipeline_flow.py` (2): fluxo
  isolado completo Grafana payload → 202 → pipeline real → postmortem →
  Slack fake recebe pipeline.completed com root_cause/postmortem/payment-svc;
  webhook sem auth → 401.
- `_queue_worker` dormir 3600s NÃO era bug (fila drenada por
  `_release_slot_or_continue` sob o mesmo lock) — documentado, sem mudança.

### F3-4 Demo: auth mutação + Slack opt-in — done
- `require_demo_token` (Depends) nos 6 endpoints de mutação (start/stop/reset/
  compare enable/disable + PATCH speed): com AIOPS_DEMO_TOKEN configurado,
  header X-Demo-Token obrigatório (hmac.compare_digest); sem a env var, modo
  dev aberto (bind default 127.0.0.1) com warning no startup.
- **Decisão registrada:** enforcement opt-in via env para não quebrar o uso
  local do dashboard (UI não envia token); exposição além de localhost exige
  configurar o token (documentado no README).
- Slack: `_build_slack_notifier()` — dry-run por padrão; real só com
  SLACK_BOT_TOKEN E AIOPS_DEMO_SLACK_REAL=1 (antes bastar o token no ambiente
  / .env.local auto-carregado disparava envio real).
- `.env.example` + README atualizados (também com AIOPS_WEBHOOK_SLACK_REAL e
  SLACK_API_BASE_URL do mock).
- Testes: `tests/test_demo_auth.py` (6): 401 sem/com token errado, 200 com
  token, modo dev aberto, dry-run default + opt-in, None sem token.

### F3-5 Rotas órfãs knowledge/admin: MONTADAS — done
- **Decisão registrada:** montar (não deletar) — as implementações reais
  existem: `rag/knowledge_store.py` (JSON store) e `ValidationRunner` com
  3 cenários isolados. Código morto com rota fantasma eliminado pelo wiring.
- `knowledge.py`: placeholder NotImplementedError substituído por provider
  real (Request → app.state.knowledge_store; 503 se não inicializado;
  dependency_overrides dos testes existentes seguem funcionando).
- `create_app`: app.state.knowledge_store = KnowledgeStore() + include_router
  knowledge e admin.
- Testes: `tests/test_knowledge_admin_mounted.py` (2 integration): GET/POST
  patterns funcionais com store isolado (tmp_path); POST admin/validate/knowledge
  executa os 3 cenários e retorna relatório.

### Nota de ambiente (descoberta durante a Fase 3)
- O .venv local era Python 3.13 com deps mínimas — requirements.txt pin
  torch<2.5 (sem wheel cp313), então faiss/torch/sentence-transformers
  NUNCA estiveram instalados; smokes com RAG degradavam graciosamente.
- **Ação (aprovada pelo usuário):** .venv recriado com `uv venv --python 3.12
  --clear` + requirements.txt completo + dev group + maturin. Ambiente
  canônico agora: 3.12.14 / ST 3.4.1 / torch 2.4.1 / faiss ok.
- Re-verificação real: smoke V1 COM RAG (`--no-llm --pipeline-v1`) exit 0,
  "Successfully loaded faiss", ZERO rag.index_build_failed → F2-2 confirmado
  end-to-end com índice real. Cenários de validação admin (FAISS+embeddings)
  passam em subprocesso (3/3).
- Lição: o teste admin que chamava a rota em processo carregaria ML no
  sys.modules do pytest e violaria TestMLIsolation → roda em subprocesso.

### Verificação da fase
- ruff ✓, mypy ✓, unit: **753 passed, 10 skipped**; integration/snapshot:
  **148 passed** (venv 3.12 canônico)
- Smokes: V0 exit 3, V1 exit 0, V1+RAG real exit 0. **Diffs de snapshot: nenhum.**
- CI completo (`scripts/ci-local.sh`): registrado no commit da fase.

### Testes adicionados na fase
- 20 testes (9 F3-1 [reescritos+novos] + 1 F3-2 + 2 F3-3 + 6 F3-4 + 3 F3-5)
  + 3 un-skipped (payload validation).

---

## Fase 4 — Segurança antes de exposição de rede — DONE (2026-11-17)

Fase iniciada em uma sessão (F4-1..F4-4 no working tree, 1 teste vermelho,
F4-5 não iniciada) e concluída em outra. Cada item: teste de regressão +
descrição do que o diff realmente faz.

### F4-1 PKCE real (authorize/token separados) — done
- `POST /auth/token/register` (verifier E challenge na mesma request) REMOVIDO.
  Substituído por dois endpoints em `auth/server.py`:
  1. `POST /auth/authorize` — recebe só `code_challenge` (S256 obrigatório),
     valida allow-list fechada (401) e scopes (403), emite `code` de uso único
     (`secrets.token_urlsafe(32)`, TTL 300 s) vinculado a
     client+scopes+challenge+audience via `_token_store.save_code`.
     `AuthorizeRequest` NÃO tem campo `code_verifier` — challenge na request de
     token e verifier na de authorize são rejeitados pelo schema.
  2. `POST /auth/token` — `pop_code` CONSOME o code (replay → 400), exige o
     mesmo `client_id` do vínculo, exige `code_verifier` que confira com o
     challenge armazenado; scopes vêm do code, não da request.
- `auth/pkce.py`: `verify_challenge` usa `hmac.compare_digest` (antes `==`).
- `auth/token_store.py`: Protocol ganhou `save_code`/`pop_code`;
  `InMemoryTokenStore` implementa com expiração absoluta e pop atômico.
  `save_challenge`/`pop_challenge` mantidos sem callers em produção (API legada
  coberta só por teste) — candidatos a remoção na Fase 5.
- Metadata coerente: discovery publica `authorization_endpoint`,
  `token_endpoint=/auth/token`, `grant_types_supported=["authorization_code"]`
  (antes `client_credentials`). Agent Card A2A anuncia flow `authorizationCode`
  com `authorizationUrl`/`tokenUrl`.
- **Decisão registrada:** `token_endpoint_auth_methods_supported` continua
  `["none"]` — cliente público com PKCE como prova de posse; não há
  client_secret no /token. Segredo por cliente existe só no modo header (F4-2).
- Testes: `tests/test_pkce_flow.py` (15): fluxo completo; verifier
  errado/ausente → 400; challenge no /token rejeitado; replay do code; code
  vinculado ao client; code desconhecido; 401/403/400 do authorize;
  `/auth/token/register` → 404/405; discovery anuncia two-step;
  `compare_digest`; store one-shot + TTL. Adaptados: `test_oauth_mcp.py`
  (4 `test_register_*` → two-step), `test_a2a_simulation.py` (`_get_token`
  two-step; asserts do agent card).

### F4-2 Autenticação de cliente no modo header — done (opção "segredo por cliente")
- **Decisão registrada:** implementado SEGREDO por cliente; o default
  `auth_method` segue `"header"` (`config/loader.py`, `config/defaults.yaml`) —
  mudar o default quebraria o uso local sem ganho, já que header agora exige
  credencial.
- `config/loader.py`: `OAuthClientConfig.secret: str = ""` (vazio = cliente não
  autentica, fail-closed). `config/defaults.yaml`: `secret: ""` nos 3 clientes +
  comentário de como configurar (YAML local ou env
  `AIOPS_MCP__OAUTH__ALLOWED_CLIENTS` JSON).
- `interfaces/mcp/sse.py` `_authenticate`: além de `X-Client-Id` na allow-list,
  exige `X-Client-Secret`; `hmac.compare_digest`; secret configurado vazio OU
  header ausente OU mismatch → 401.
- **Impacto operacional:** com o YAML padrão NENHUM cliente autentica no modo
  header até configurar segredo — fail-closed deliberado, documentado.
- Docs: README / README.pt-BR (nova subseção de autenticação MCP/A2A cobrindo
  `oauth21`, `header`, `federated` e vínculo de sessão), `.env.example` (bloco
  MCP/A2A com `AIOPS_MCP__OAUTH__JWT_SECRET` e `ALLOWED_CLIENTS`). Corrigido
  erro pré-existente nos READMEs: o prefixo de env é `AIOPS_` + `__` entre
  níveis (`AIOPS_MCP__OAUTH__JWT_SECRET`), não `AIOPS__MCP__...`.
- Testes: `tests/test_header_client_auth.py` (5): sem secret → 401; errado →
  401; correto passa (≠401); cliente sem secret configurado → 401; id
  desconhecido → 401.

### F4-3 JWT iss/aud + RS256 real + kid/kty no OIDC — done
- `auth/jwt_validator.py`: `issue_token` ganha `issuer`/`audience` e emite
  `iss`/`aud`; `validate_token` aceita e repassa ao `jwt.decode`; `TokenClaims.aud`.
- Audiência escolhida no `/authorize` (`"mcp"` | `"a2a"`, 400 fora disso),
  guardada no code e emitida no `/token` com `issuer=_base_url or
  "aiops-agent"`. Introspecção aceita ambas as audiências mas exige `iss`.
- MCP exige `audience="mcp"` (`sse.py`); A2A exige `audience="a2a"` (`a2a.py`)
  → token MCP não vale no A2A e vice-versa.
- **Decisão registrada: RS256 implementado de verdade (não removido do
  metadata).** `_signing_algorithm()` → RS256 quando o par RSA está
  configurado; `_signing_key_ready()` aceita HMAC ≥32 bytes OU RSA → RSA +
  secret curto não estoura mais `InvalidKeyError`/500 (503 só sem nenhuma
  chave). JWKS publica a chave realmente usada. Consumidores validam RS256
  quando há `public_key_path` (sse) / `app.state.a2a_jwt_public_pem` (a2a).
- `auth/oidc_validator.py`: kid desconhecido → `invalidate_cache` + refresh
  ÚNICO do JWKS e, se ainda ausente, rejeita; sem fallback `keys[0]` (kid
  ausente só com JWKS de 1 chave); algoritmo escolhido pelo `kty` (RSA/EC,
  outro → rejeita).
- Nota: em produção `create_app` passa `base_url=""` → `iss` emitido é o
  fallback `"aiops-agent"`; coerente com a validação, mas não é a URL pública
  (pendência menor para a Fase 5/composition root).
- Regressões da sessão anterior corrigidas aqui: `test_introspect_valid_token`
  emitia token com `iss` default enquanto o servidor valida `iss=base_url`
  (teste alinhado ao contrato, não o contrário); `test_a2a_simulation.py`
  emitia 8 tokens com `aud` default `mcp` contra rota que exige `a2a` → 22
  falhas; `_bearer()`/`_get_token()`/one-liners agora pedem `audience="a2a"`.
- Testes: `tests/test_jwt_audience.py` (7); `TestKidHardening` em
  `test_oidc_validator.py` (5). Ajustados: `test_oauth_mcp.py`,
  `test_jwks_endpoint.py`, `test_federated_skill_invocation.py`.

### F4-4 Databricks: SQL parametrizado — done
- `tools/databricks_connector.py` `_execute_query(sql, parameters)`:
  marcadores nomeados `:name` + `StatementParameterListItem` da Statement
  Execution API, tipo inferido (BOOLEAN/INT/DOUBLE/TIMESTAMP/STRING). Valores do
  usuário NUNCA no texto SQL; identificadores de tabela/coluna vêm da config e
  são concatenados (não parametrizáveis).
- Parametrizados: `search`, `tail`, metrics `query`, `find_traces`, `get_trace`.
  Escape manual `replace("'", "''")` removido (inclusive a variável órfã
  `safe_svc` que sobrou em `find_traces` — F841). `get_alerts` sem parâmetros:
  `'active'` é constante do código.
- Aceite: `rg -n 'f".*(SELECT|LIKE|LIMIT)' tools/databricks_connector.py` → vazio.
- Testes legados de SQL (13 em `test_databricks_connector.py`) migrados para o
  contrato parametrizado: helper `_capture(source)` devolve `(sql, params)`;
  asserts verificam placeholder no texto + valor em `params` + **ausência** do
  valor no texto. Antes verificavam `"timeout" in sql` — ou seja, exigiam a
  interpolação que o fix remove.
- Residual conhecido: curingas LIKE (`%`/`_`) no input não são escapados —
  afeta semântica de busca, não é injeção.
- Testes: `tests/test_databricks_parameterized.py` (7): payload de injeção só
  em params; timestamps; tail; metrics; find_traces; get_trace; `_execute_query`
  repassa `parameters` ao SDK com tipos corretos.

### F4-5 chmod 0600 na chave privada + sessão MCP vinculada ao cliente — done
- `auth/keys.py`: `_write_private_key` cria o arquivo já com 0600 via
  `os.open(O_WRONLY|O_CREAT|O_TRUNC, 0o600)` + `chmod` (sem janela entre criar
  com umask default e restringir; umask permissivo não afrouxa).
  `_restrict_private_key_permissions` corrige chave pré-existente frouxa no
  load, sem tocar o conteúdo. Pública segue legível.
- `interfaces/mcp/sse.py`: `_resolve_principal` unifica a autenticação de GET
  `/mcp/sse` e POST `/mcp/messages` (inclusive o fluxo federated, que estava
  duplicado nos dois handlers); `_bind_session_principal` define
  `request.scope["user"] = AuthenticatedUser(AccessToken(client_id, scopes,
  subject=client_id))`. O `SseServerTransport` do SDK registra o principal de
  quem abriu a sessão e, no POST, compara `authorization_context` — mismatch
  → 404 "Could not find session" (indistinguível de sessão inexistente; não
  revela que o `session_id` é válido).
- **Decisão registrada:** reaproveitar o mecanismo nativo do SDK
  (`_session_owners`) em vez de manter um mapa paralelo sessão→cliente — um
  único ponto de verdade, e o 404 uniforme já vem pronto.
- Testes: `tests/test_mcp_session_binding.py` (4): mesmo cliente → 202; outro
  cliente autenticado → 404 com corpo idêntico ao de sessão inexistente; sem
  credencial → 401 antes do lookup; binding depende do client_id (não é
  constante). `tests/test_jwks_endpoint.py` +3: 0600 na criação; 0600 com umask
  0o000; chave 0644 pré-existente apertada no load sem mudar conteúdo.
  (Os testes de sessão não abrem stream SSE real: registram no transporte o
  mesmo principal que o GET constrói, via `_principal_user`, e exercitam o
  POST de verdade.)

### Verificação da fase
- Executada no M1 Docker (`ssh m1-docker`, `/tmp/iago-ci-m5/aiops-agent`,
  Python 3.12.9, requirements completos) — regra "nada pesado no M5".
- ruff ✓, ruff format ✓, mypy ✓ (domain/application/core)
- unit: **783 passed, 11 skipped**; integration/snapshot: **148 passed, 2 skipped**
- Smokes: V0 exit 3, V1 exit 0, V1+RAG real exit 0 (zero `rag.index_build_failed`).
  **Diffs de snapshot: nenhum.**

### Testes adicionados na fase
- 46 testes novos: 15 F4-1 + 5 F4-2 + 12 F4-3 (7 audience + 5 kid hardening) +
  7 F4-4 + 7 F4-5 (4 session binding + 3 chmod).
- Reescritos para o contrato novo (sem contagem nova): 13 testes de SQL em
  `test_databricks_connector.py`; `test_oauth_mcp.py`, `test_a2a_simulation.py`,
  `test_jwks_endpoint.py`, `test_federated_skill_invocation.py`,
  `test_oidc_validator.py`.

---

## Fase 5 — Convergência arquitetural — EM ANDAMENTO (2026-11-17)

Ordem executada: F5-3 primeiro (config drift + loader de env são pré-requisito
para mover código de `main.py` sem carregar os parsers duplicados junto), depois
F5-1 (status do plano) e F5-2 (god-file), por fim F5-4 (remoção do V0).

### F5-3 Composition root + config drift — done
- **Loader único de env**: `config/env.py` `load_local_env(root=None)` via
  `python-dotenv` (já em `requirements.txt`). Substitui 5 parsers manuais
  divergentes: `main.py` (V0 e V1), `interfaces/demo/server.py`,
  `infra/run_webhook_server.py`, `infra/test_slack_notification.py`.
  Precedência preservada do V0 (`load_dotenv()` + `setdefault`): ambiente do
  processo > `.env` > `.env.local`; nenhum arquivo sobrescreve variável já
  definida.
- **Mudanças de comportamento intencionais (documentadas):** (a) `.env` passa a
  ser carregado por TODOS os entrypoints — antes só o CLI V0 o lia; (b) valores
  entre aspas no `.env.local` perdem as aspas (semântica dotenv; o parser
  manual as mantinha literalmente — era bug latente para tokens com aspas).
- `tests/test_exit_codes.py`: 5 `patch("main.load_dotenv")` →
  `patch("main.load_local_env")`.
- **Config drift fechado** (`config/loader.py`, `config/defaults.yaml`):
  - `slack.webhook_url` (campo fantasma, descartado por `extra="ignore"`)
    REMOVIDO; bloco `slack` do YAML agora espelha `SlackConfig` (channel,
    max_retries, initial_backoff_s, backoff_multiplier, fallback_dir, dry_run)
    com comentário explícito: token NUNCA vem do YAML.
  - `WebhookConfig` ganha `max_payload_bytes`, `dedup_window_seconds`,
    `max_concurrent_pipelines`, `max_queued_alerts` + validators (>0 / >=0).
    Antes: `router.py` lia os dois primeiros via `getattr(..., default)` — o
    fallback era SEMPRE usado e qualquer valor no YAML era descartado;
    `run_webhook_server.py` hardcodava 3/10; `tests/epic2/conftest.py` já
    referenciava os campos num fixture nunca executado (teria dado
    AttributeError).
  - `interfaces/http/webhook/router.py`: lê `config.webhook.*` tipado; as
    constantes `DEFAULT_*` ficam só para app SEM config em `app.state`.
  - `infra/run_webhook_server.py`: `PipelineRunner(max_concurrent=config.webhook
    .max_concurrent_pipelines, max_queued=config.webhook.max_queued_alerts)`.
- `tools/slack_setup.py`: `config.slack` deixa de ser "reservado para uso
  futuro" — repassa max_retries/backoff/fallback_dir/dry_run ao `SlackNotifier`;
  canal: `config.slack.channel` > `SLACK_CHANNEL`; token sempre do ambiente;
  falha de init vira `log.warning("slack.notifier_init_failed")` em vez de
  `except: return None` mudo. `main_v1` passa `app_config`.
  **Decisão registrada:** `config.slack.enabled` continua NÃO consultado pela CLI
  (token+canal é o opt-in, contrato do README); `enabled` governa o observer do
  HTTP app (`interfaces/http/app.py`). Residual: `main()` V0 ainda chama
  `_setup_slack()` sem config — V0 será removido em F5-4, não vale tocar.
- `.env.example`: bloco Jira (`JIRA_URL/EMAIL/API_TOKEN/PROJECT_KEY`) adicionado;
  `SLACK_API_BASE_URL` e `AIOPS_MCP__OAUTH__JWT_SECRET` já constavam (F3-4/F4).
- **Composition root — estado honesto:** `app/builders.py` é usado por TODO
  entrypoint que instancia o engine (CLI V1, HTTP app → MCP SSE e A2A, webhook
  server). Demo não instancia o engine (tem simulação própria, `DemoAnalyzer`);
  MCP stdio (`run_stdio`) é dev-only e não constrói engine (limitação
  pré-existente, já listada em F6-5/F3). CLI V0 segue inline até F5-4.
- `MitigationAction`: já era tipo único (`domain/incident.py`; `safety/tnr.py`
  re-exporta) — nada a fazer, conforme verification_note.
- Testes: `tests/test_config_env.py` (5): .env antes de .env.local sem
  override; ambiente vence; arquivos ausentes = no-op; comentários/espaços/aspas;
  raiz default. `tests/test_slack_setup.py` (7): sem token/canal → None;
  valores de config repassados; precedência canal config > env; compat sem
  config; falha de init logada. `tests/test_config_drift.py` (8): YAML sem
  chave desconhecida em slack/webhook; campos operacionais do YAML; override
  por env; 4 validações; bloco slack espelha defaults.

### Verificação (F5-3)
- M1 Docker: ruff ✓, ruff format ✓, mypy ✓; unit **803 passed, 11 skipped**;
  integration/snapshot **148 passed, 2 skipped**; smokes V0 exit 3 / V1 exit 0;
  `create_webhook_app()` monta com `max_concurrent=3, max_queued=10` vindos do
  YAML. **Diffs de snapshot: nenhum.**
- Aceite `rg '.env.local' --type py`: lógica de parsing só em `config/env.py`;
  ocorrências restantes são comentários/docstrings e o rótulo de status do
  webhook server. Nenhum `getattr(config.webhook, ...)` restante.

### F5-2 Quebrar o god-file `main.py` — done

Executado em 8 commits, um por passo lógico, sem misturar movimentação com
comportamento (regra do plano). Estratégia: **o que F5-4 vai apagar vai em
bloco para um único módulo legado, sem reescrita; o que precisa sobreviver ao
V0 ganha casa definitiva agora.**

| # | Commit | Conteúdo |
|---|--------|----------|
| 1 | `2a1198f` | Deleções: `_run_v0` (stub), `get_pipeline` + `core/pipeline_protocol.py` (zero consumidores), chamada morta `step_predict(report, memory_db=)` (TypeError engolido por `except: pass`) |
| 2 | `0a7a6af` | `interfaces/cli/{console,exit_codes,args}.py`, `core/secrets.py`. `--tracker` choices passam a vir de `TRACKER_CHOICES` estático (parser não depende das factories) |
| 3+4 | `0a3b35e` | `application/use_cases/postmortem.py` (template md + serialização JSON, puro, mypy-checado; `now` injetado), `interfaces/cli/{postmortem,trackers,data_sources}.py` |
| 5+6 | `7852646` | `interfaces/cli/display.py` (banner, summary, results), `interfaces/cli/v1_runner.py` (`create_incident_state`, `run_v1`) |
| 7 | `96df3d9` | `interfaces/cli/v0_pipeline.py`: `step_*` + `run_v0` verbatim; `main.py` vira entrypoint fino; patches de teste retargetados |
| 8 | (este) | Deduplicações: `DiagnosisAgent.heuristic_diagnosis` público (alias `_heuristic_diagnosis` mantido), `domain/confidence.py` (3 cópias → 1), `AgentOrchestrator.run(..., detection=)` |

- **Achados além do plano (verificados):** a chamada morta de `step_predict`
  tinha assinatura errada e nunca imprimiu nada; em modo LLM o V0 detectava
  2× **com thresholds diferentes** (CLI com `config.thresholds`, orchestrator
  com `DetectionAgent()` default) — corrigido por `run(detection=...)`; o mapa
  de confiança existia 3× (V0 ×2 + MCP), com semânticas sutilmente distintas
  para numéricos/desconhecidos.
- **Decisão registrada:** o template de postmortem foi para `application/`
  (não para `interfaces/cli/`) porque é lógica pura e o V1 precisa dele em
  F5-4 (o template do node V1 em `pipeline_nodes.py` é mais pobre). Protegido
  por `tests/snapshots/postmortem_structure.txt` — inalterado.
- **Decisão registrada:** `get_pipeline`/`PipelineProtocol` removidos já em
  F5-2 (F5-4 os listava): `make_pipeline(main_v1).run(args) ≡ main_v1(args)` e
  o ramo V0 da factory era o stub morto.
- **Compat block** em `main.py`: um import por linha com `noqa: F401` próprio
  — na primeira tentativa `ruff --fix` apagou os não marcados e 9 nomes
  sumiram; o bloco inteiro é removido em F5-4.
- Testes: `test_exit_codes.py`/`test_llm_model_config.py` patcham
  `interfaces.cli.v0_pipeline.*` (canário `test_main_returns_one_on_exception`
  segue dando 1); `test_api_key_safety.py` inspeciona
  `render_postmortem_markdown` além de `step_postmortem` (senão passaria
  vazio). **Bug latente exposto:** dois testes de `test_llm_model_config.py`
  mockavam `DetectionAgent` com `MagicMock()` cru — truthy em
  `.get("is_incident")`; com `run(detection=...)` isso levava ao caminho LLM
  com cliente Anthropic real (hang de rede, pegou a suíte no M1). Corrigido
  para devolver `{"is_incident": False}`.
- Docs atualizadas: `architecture.md`, `api-contracts-internal.md`,
  `development-guide.md`, `source-tree-analysis.md`, `data-models.md`
  (referências `main.py:step_*` → novos caminhos).
- Testes novos: `test_confidence.py` (9), `test_orchestrator_detection.py` (3).
- `main.py`: **2582 → 129 linhas** (meta < 300). `interfaces/cli/v0_pipeline.py`
  tem ~1230 — intencional, é o que F5-4 apaga.

### F5-1 Plano de refatoração: status + fase 5 — done
- Conforme verification_note, 2b/2c/2d/2e/3/4 já estavam executadas; faltava
  só a fase 5 parcial. `docs/refactoring-execution-plan.md` ganhou tabela
  **Status (2026-11-17)** com evidência por fase (comandos de aceite) e nota
  na fase 5.
- `app/builders.py` ganha `build_detector_agent`, `build_diagnoser_agent`,
  `build_agent_orchestrator` e `resolve_llm_model` (env `AIOPS_LLM_MODEL` >
  `config.llm.model_name` — antes o default `claude-sonnet-4-20250514` estava
  hardcoded em 3 call sites, ignorando `LLMConfig`). Imports de `agents.*`
  lazy: o módulo é importado por composition roots sem agentes e os testes do
  V0 patcham as classes em `interfaces.cli.v0_pipeline`.
- `blueprint/adapters/pipeline_nodes.py` `make_diagnoser_node` usa os
  builders: **o V1 passa a detectar com `config.thresholds`** (antes só defaults
  via orchestrator) e reutiliza a detecção — paridade com a CLI, pré-requisito
  de F5-4. Import de `app.builders` lazy dentro do node (ciclo
  `builders → pipelines → nodes` + isolamento de ML na coleta).
- **Decisão registrada:** o V0 (`v0_pipeline.py`) NÃO migra para os builders —
  será apagado em F5-4 e seus testes dependem de patchar as classes naquele
  namespace.
- Testes: `test_builders_agents.py` (8): env vs config do modelo; thresholds no
  detector; kwargs do diagnoser; orchestrator com cliente/memória injetados
  (não cria Anthropic) e sem (cria com `ANTHROPIC_API_KEY`); node V1 detecta
  com config e passa `detection=` ao orchestrator.

### Verificação (F5-1 + F5-2)
- M1 Docker: ruff ✓, ruff format ✓, mypy ✓ (17 arquivos); unit **823 passed,
  11 skipped**; integration/snapshot **148 passed, 2 skipped**; smokes V0 exit 3
  / V1 exit 0 / V1+RAG real exit 0 (zero `rag.index_build_failed`).
  **Diffs de snapshot: nenhum.**
- `pytest-timeout` instalado no venv do M1 (`--timeout=120`) após o hang
  descrito acima — recomenda-se adicionar ao CI (F6).
- Aceite F5-2: `wc -l main.py` = 129 (< 300) ✓; `rg "from anthropic|import
  anthropic"` só em `adapters/llm/anthropic_client.py` ✓; `rg "_heuristic_
  diagnosis"` fora de `agents/diagnoser.py`/tests: só docstrings em
  `data/unknown_fault_scenario.py` ✓; mapa `{"high": 0.85, ...}` só em
  `domain/confidence.py` ✓.

### F5-4 Aposentar o V0 — BLOCKED (decisão do usuário, 2026-11-17)

O aceite exige "golden/snapshots equivalentes ANTES da remoção". Medido no
M1 com o mesmo cenário (`--no-llm --no-rag`), a paridade **não existe**:

| | V0 (default) | V1 (`--pipeline-v1`) |
|---|---|---|
| Exit code | **3** (human gate, `requires_human`) | **0** ("resolvido") |
| Postmortem | 92 linhas, 11 seções (Root Cause, Mitigation, Safety, CVE, TNR…) | 26 linhas, 5 seções |
| Flags honradas | todas | ignora `--data-source`, `--tracker`, `--output-format`, `--rebuild-rag`, `--output-dir` |
| Só no V0 | estágio preditivo, cache FAISS em disco, memória episódica (`IncidentRecord`), evolução de skills, Slack progressivo (5 eventos vs 2), tabela-resumo | — |

- `tests/conftest.py::_run_pipeline` espelha o V0 manualmente e alimenta 15/17
  snapshots + golden validation; `test_exit_codes.py` testa os exit codes do V0.
- Remover o V0 hoje quebraria o contrato público de exit codes do README
  (3 = safety blocked) e removeria 6 features sem substituto — viola a regra 4
  ("não quebre o V0 até paridade").
- **Diagnóstico:** F5-4 não é remoção mecânica; é portar o V0 para o V1.
  Sub-features propostas para um épico próprio: 4a human gate + exit codes no
  `run_v1`; 4b postmortem rico via `application/use_cases/postmortem.py` (já
  extraído em F5-2 para isso); 4c `--tracker`/`--data-source`/`--output-format`
  /`--rebuild-rag`/`--output-dir`; 4d Slack progressivo + preditivo + memória/
  skills; 4e `conftest._run_pipeline` → V1 e snapshots regenerados com diff
  justificado; 4f remoção de `v0_pipeline.py`, flag e compat block.
- **Estado deixado:** o V0 está isolado em `interfaces/cli/v0_pipeline.py`
  (único módulo), `main.py` tem 129 linhas, e nada mais depende dele — o
  bloqueio não impede a Fase 6.
- Decisão do usuário: seguir para a Fase 6 (opção 3 de 3 apresentadas).
---

## Fase 6 — Paridade Rust/Python, features mortas, caches, testes Rust — EM ANDAMENTO (2026-11-17)

### F6-1 Paridade Rust↔Python — done
- **Ponte PyO3 parametrizada** (`rust/src/metric_filter/bridge.rs`,
  `rust/src/trace_filter/bridge.rs`): `filter_metrics(metrics, sigma_threshold=3.0,
  baseline_ratio=0.5)` e `filter_traces(spans, percentile_threshold=95.0,
  max_hops=3)` via `#[pyo3(signature=...)]`, com validação (`ValueError` para
  sigma ≤ 0, ratio ∉ (0,1), percentil ∉ [0,100], hops = 0). O núcleo Rust
  (`detect_anomalies`/`filter_slow_traces`) já era paramétrico — só a ponte
  hardcodava os defaults.
- `pipeline/metric_filter.py` / `pipeline/trace_filter.py`: Rust para QUALQUER
  threshold (antes: `if _RUST_AVAILABLE and sigma == 3.0 and ratio == 0.5` →
  config custom caía no Python sem aviso, com algoritmo potencialmente
  divergente). **Decisão registrada:** implementar os parâmetros no Rust em vez
  de só avisar — o aviso deixaria dois caminhos vivos para a mesma config.
- `pipeline/telemetry_sanitizer.py`: `_use_rust()` — com `custom_patterns` o
  Rust (padrões base em `OnceLock`, estáticos) é ignorado e o Python assume,
  com **um** `log.warning("sanitizer.rust_fallback_custom_patterns")`. Antes o
  path Rust (single E batch) ignorava os padrões custom: payload casado só por
  padrão custom passava como CLEAN. **Decisão registrada:** fallback+warning em
  vez de passar os padrões ao Rust — exigiria recompilar regex por chamada no
  hot path e `custom_patterns` não tem nenhum uso em produção hoje.
- `pipeline/log_parser.py`: semântica unificada — **ambos processam todos os
  logs** (o fallback amostrava ≤500 via `logs[::step]`; o cenário default tem
  503, então já perdia entradas) e **ambos são sem estado entre chamadas**
  (Python acumulava `self.templates` → contagens dobravam no 2º `parse`).
  `get_error_templates()` não chama mais `self.parse([])` (Rust devolvia `[]`,
  Python os acumulados); filtra a lista passada ou o último `parse`.
  `_SAMPLE_THRESHOLD`/docstring de stride obsoletos removidos.
- Testes: `tests/rust/test_parity.py` (28): mesmo input → mesmo output nos dois
  paths para 4 combinações de thresholds de métrica e 4 de trace; spy confirma
  que config custom chega ao Rust; ponte rejeita parâmetros inválidos; 7 amostras
  de taint iguais nos dois paths; custom pattern honrado (single+batch), warning
  único, Rust usado sem custom; log parser: 503/503 no fallback, mesmas contagens,
  stateless nos dois, `get_error_templates`/`to_text` funcionam nos dois.

### Verificação (F6-1)
- M1 Docker, extensão Rust compilada (`maturin develop --release`): `cargo test`
  15 passed; `tests/rust` 65 passed (3 slow deselected); ruff/format/mypy ✓;
  unit **869 passed** (com Rust instalado os `importorskip` entram); integration/
  snapshot **148 passed**; smokes V0 exit 3 / V1 exit 0. **Diffs de snapshot:
  nenhum** (snapshots usam os defaults, que já iam para o Rust).
- Nota: `cargo fmt --check` reporta diffs em 20 arquivos pré-existentes — fica
  para F6-4 (clippy/fmt no CI), num commit só de formatação.
