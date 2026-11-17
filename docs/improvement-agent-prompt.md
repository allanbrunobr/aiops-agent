# PROMPT — Plano de Melhorias do aiops-agent (para agente executor)

> Copie tudo abaixo desta linha e entregue ao agente. O prompt é autocontido.

---

Você é um engenheiro sênior trabalhando no repositório `aiops-agent` (Python 3.11+ / Rust PyO3), um protótipo de plataforma AIOps: recebe alertas, processa telemetria (logs/métricas/traces), detecta e diagnostica causa-raiz com LLM (Claude) + RAG (FAISS), propõe mitigação com camadas de segurança e aprovação humana, e gera postmortem + notificações Slack/Jira. **Por design, nenhuma ação é executada em infraestrutura real** — o agente propõe, valida e aguarda humano (exit code 3). Não mude esse contrato.

Uma auditoria completa já foi feita. Sua tarefa é executar o plano de correções abaixo, na ordem, com os critérios de aceite definidos. Todos os achados têm `file:line` verificados — confirme-os antes de editar (linhas podem ter deslocado).

## Contexto arquitetural mínimo

- **Dois engines**: V0 (default) orquestrado por funções `step_*` em `main.py` (2247 linhas); V1 (`--pipeline-v1`) usa `BlueprintEngine` (`blueprint/engine.py:58`, assinatura `run(pipeline, state: IncidentState)`), 8 nodes com políticas ABORT/SKIP/FALLBACK, estado imutável (`domain/incident_state.py`, frozen dataclass).
- **Camadas hexagonais**: `domain/` puro → `application/ports/` (LLMClient, IncidentStore, CredentialProvider, Reporter) → `adapters/` → `interfaces/`. Composition root: `app/builders.py` (hoje só o V1 usa). Regra: adapters importam de pipeline/agents/rag; o inverso NUNCA.
- **Rust opcional** `_aiops_rust` (PyO3): todo consumidor tem fallback Python via `try: import _aiops_rust / except ImportError`.
- **Testes**: pytest com markers `unit`/`integration`/`snapshot`/`slow` (`pyproject.toml:12-17`); `tests/conftest.py:33-40` bloqueia torch/sentence_transformers/faiss na coleta; `_run_pipeline` roda o pipeline real com `--no-llm --no-rag` (`tests/conftest.py:66-150`); snapshots normalizados (`tests/snapshot_utils.py:20-50`, flag `--snapshot-update`); golden validation em `validation/runner.py`.
- **CI**: `.github/workflows/ci.yml` — 3 jobs (unit deps mínimas; integration+snapshot deps completas; rust cargo test + maturin build + suíte completa). Réplica local: `scripts/ci-local.sh`.
- **Plano de refatoração existente**: `docs/refactoring-execution-plan.md` (fases 2b–5; 2e e 4 já executadas). Este plano o ESTENDE, não o substitui. ADRs em `docs/adr/` são vinculantes.

## Regras rígidas de execução

1. **Uma fase por vez, um PR (ou commit coeso) por fase.** Não misture movimentação de arquivos com mudança de comportamento.
2. **Teste que falha ANTES do fix** para cada bug: escreva o teste de regressão, veja-o falhar, corrija, veja-o passar.
3. Antes de cada push: `scripts/ci-local.sh unit`. Antes de concluir cada fase: CI completo (unit + integration/snapshot + rust).
4. **Não quebre o V0** enquanto o V1 não tiver paridade (Fase 5 define o critério de remoção).
5. Não adicione features novas. Não faça rewrite do zero. Não mexa no Pydantic de `domain/` sem ADR aprovado (regra de `docs/refactoring-execution-plan.md:19-20`).
6. Shims só como transição, cada um com critério de remoção explícito.
7. Ao atualizar snapshots, justifique cada diff no commit — snapshot mudou = comportamento mudou, e isso precisa ser intencional.
8. Se um achado não se confirmar no código atual, documente e pule — não invente trabalho.
9. Ao final de cada fase, registre status (done/skipped/blocked + motivo) em `docs/improvement-progress.md`.

## Comandos de verificação padrão

```bash
.venv/bin/python -m pytest tests -m unit -q          # ~692 testes, ~2s
scripts/ci-local.sh unit                              # réplica CI local
scripts/ci-local.sh                                   # CI completo
.venv/bin/python main.py --no-llm --no-rag            # smoke V0 (exit 0 ou 3)
.venv/bin/python main.py --no-llm --no-rag --pipeline-v1  # smoke V1
```

---

## FASE 0 — Rede de proteção (CI/lint/docs)

Tarefas:
- [ ] Adicionar `ruff` (check + format) e `mypy` ao `pyproject.toml` e como job/dev-dependency. Ruff: regras padrão + imports; mypy: começar SOMENTE por `domain/`, `application/`, `core/` (módulos puros), com override permissivo para o resto.
- [ ] Adicionar os novos checks ao `.github/workflows/ci.yml` (job lint separado) e a `scripts/ci-local.sh`.
- [ ] Adicionar cache de pip (`actions/setup-python` com `cache: pip`) nos 3 jobs existentes.
- [ ] Adicionar coverage report (sem gate de threshold) ao job de testes.
- [ ] Corrigir `docs/project-overview.md` — está obsoleto (afirma "Tests: None" e "~4900 lines"; realidade: 94 arquivos de teste, ~53k linhas Python).
- [ ] Documentar os novos comandos em `CONTRIBUTING.md`.

Aceite:
```bash
ruff check . && ruff format --check .
mypy domain application core
scripts/ci-local.sh unit
```
CI verde com o job de lint; nenhum erro novo de mypy nos módulos puros.

---

## FASE 1 — Bugs do caminho padrão (V0)

Cada item = 1 teste de regressão + 1 fix.

### 1.1 Mapeamento FAISS corrompido após carregar cache
- `rag/indexer.py:197-261` (`load_index`): restaura chunks e índice mas NÃO reconstrói `_faiss_to_chunks` (mapa criado em `rag/indexer.py:131-133`). O índice FAISS contém só textos únicos; `chunks` contém duplicatas. `rag/retriever.py:92` cai no fallback `chunks[idx]` → resultados TROCADOS após load do cache.
- Fix: reconstruir `_faiss_to_chunks` no `load_index` (persistir o mapa junto do cache ou recomputá-lo a partir dos chunks deduplicados na mesma ordem da indexação).
- Teste: indexar report com textos duplicados → salvar cache → novo indexer `load_index` → mesma query deve retornar os MESMOS chunks/ranking do indexer original.

### 1.2 Bug `for/else` na sanitização de traces + mutação in-place
- `pipeline/telemetry_sanitizer.py:319-320`: o `else` do `for` sobre attributes executa sempre (não há `break`) → spans contaminados são contados como `clean_entries`; spans sem attributes nunca contam como clean.
- Métricas e traces são mutados IN-PLACE, ao contrário dos logs (clonados). Unifique: sanitize deve retornar cópias, nunca mutar o input.
- Teste: span com payload de injection em attribute → deve contar como contaminado, não como clean; input original intacto após `sanitize_all()`.

### 1.3 Estágio preditivo opera sobre dados errados
- `pipeline/predictive_stage.py:118`: `getattr(trace, "duration_ms", 0.0)` — o campo real de `SlowCallPath` é `total_duration_ms` → spans sintéticos sempre 0.0.
- `pipeline/predictive_stage.py:124`: coloca `path` (list) em `Span.operation` (str).
- Pontos de métrica vêm de anomalias deduplicadas a 1 por métrica (`pipeline/metric_filter.py:151-158`) → slope EWMA cruza métricas distintas. Agrupe por métrica antes de calcular slope.
- Logs sintéticos todos com `timestamp=now` — use timestamps reais quando disponíveis.
- Teste: alimentar preditivo com traces/métricas conhecidos → risk_score deve refletir os dados (não ser constante/ruído); assert de tipos nos campos sintéticos.

### 1.4 Key mismatch no reranker
- `rag/reranker.py:141` lê `metadata.get("weight")`, mas o indexer escreve `"chunk_weight"` (`rag/indexer.py:366,383,403,438`) → downweight de histórico (0.5) nunca aplicado.
- Fix: alinhar a key (prefira `chunk_weight` em ambos). Teste: chunk `[HISTORICAL]` com `chunk_weight=0.5` deve pontuar abaixo de chunk idêntico com peso 1.0.

### 1.5 Foreign keys nunca ativadas
- `rag/memory.py:187-191`: docstring promete FK mas falta `PRAGMA foreign_keys=ON` por conexão. Ativar e adicionar teste: inserir `skill_outcomes` com `skill_id` inexistente deve falhar.

Aceite da fase:
```bash
.venv/bin/python -m pytest tests -m unit -q   # todos verdes, incluindo os novos
scripts/ci-local.sh                            # completo verde
```
Snapshots atualizados apenas onde a correção muda output legitimamente (justificar no commit).

---

## FASE 2 — Tornar a safety real

### 2.1 V1: as 3 camadas de safety nunca validam nada
- Nenhum código seta `state.proposed_action` (nem `make_diagnoser_node` em `blueprint/adapters/pipeline_nodes.py`, nem `main_v1` em `main.py`). Resultado: `blueprint/adapters/safety_nodes.py:90-95,151-156,222-227` fazem skip `"no_proposed_action"` → version_validator/pre_validator/tnr passam direto, contradizendo o docstring "ALL 4 safety layers execute before any action proposal" em `blueprint/pipelines/incident_response.py`.
- Fix: o diagnoser node (e o fallback determinístico) deve preencher `state.proposed_action` com a `MitigationAction` proposta (use `dataclasses.replace`). Atenção: existem DOIS tipos `MitigationAction` (`domain.incident` vs `safety.tnr`) — use o do domain no estado e converta na borda da safety.
- Teste E2E V1: cenário com ação proposta perigosa (ex.: comando destrutivo ou versão com CVE) → pipeline deve BLOQUEAR (não resolver silenciosamente).

### 2.2 V1: RAG nasce vazio
- `app/builders.py:47-51` (`build_rag`): cria indexer sem chamar `build_index`; comentário em `main.py:1852` admite "por ora, sem report pre-pipeline".
- Fix: construir o índice a partir do report agregado disponível no pipeline (node de indexação após aggregator, ou build lazy no primeiro retrieve com o report do estado).
- Teste: V1 com RAG habilitado → retriever retorna chunks do incidente corrente.

### 2.3 TNR de teatro (3 lugares)
- `agents/mitigator.py:142-165` (`verify_with_tnr`): fabrica snapshots pré/pós hardcoded (error_rate 45.0→0.1, availability 55→99.9) → QUALQUER ação "passa"; o replan por regressão (`agents/orchestrator.py:326`) nunca dispara por esse caminho. Há também `command.format(..., current=3, prev=1)` hardcoded (`agents/mitigator.py:114,117`) e `ROLLBACK_VERSIONS` fixo (`agents/mitigator.py:25`).
- `interfaces/mcp/tools.py:479-530` (`_check_tnr`): snapshots pré/pós IDÊNTICOS hardcoded (error_rate=5.0, p99=200.0, avail=99.0).
- `safety/orchestrator.py:150-166`: chama `simulate_check` com baseline fixo idêntico pré/pós (error_rate=0.0, availability=100.0).
- O design real do TNR está pronto em `safety/tnr.py:100-172` (`execute_with_safety`: snapshot pré → exec → pós → rollback se delta < −5.0). Fix: derivar snapshots do `AggregatedReport`/fonte de telemetria (simulada ou Databricks) em vez de constantes. ONDE a simulação for intencional (PoC sem infra real), renomeie para `simulate_*`, documente explicitamente no docstring e no README que NÃO é validação real, e faça o resultado simulado ser distinguishable no postmortem/audit.
- Teste: ação que "piora" as métricas simuladas → TNR deve reportar regressão e disparar rollback/replan; o caminho de replan do orchestrator (`agents/orchestrator.py:323-326`) deve ser exercitado por um teste (hoje o acoplamento é `getattr(getattr(tnr_result,'result',None),'value',None)=='success'` — substitua por acesso tipado).

### 2.4 Fail-open em exceção → fail-closed
- `safety/orchestrator.py:125-126`: exceção no sanitizer → `passed=True` (skipped). `safety/orchestrator.py:169-170`: exceção no TNR → `passed=True`.
- `safety/pre_validator.py:319-324`: exceção de auditoria engolida.
- MCP `_sanitize_result` (`interfaces/mcp/tools.py`): `except Exception: pass` MANTÉM o texto original se o sanitizer falhar → PII vaza no caminho de erro.
- Fix: exceção em camada de safety = decisão `blocked`/`passed=False` com motivo explícito no audit; sanitizer MCP em erro = retornar texto redigido/genérico, nunca o original. Auditoria que falha deve logar erro estruturado (não silenciar).
- Teste: monkeypatch para lançar exceção em cada camada → decisão final deve ser bloqueio; nenhum `passed=True` dentro de `except` no diretório `safety/`.

### 2.5 Bypass de allowlist por substring
- `safety/pre_validator.py:119,339-341`: `re.search` de padrão read-only casa em qualquer posição → `kubectl get pods && python -c "..."` passa como read-only.
- Fix: tokenizar com `shlex.split`, rejeitar operadores de composição (`&&`, `||`, `;`, `|`, backticks, `$(`) ANTES de checar allowlist; validar o binário+subcomando do primeiro token. Manter default-deny (`safety/pre_validator.py:352-354`) e blocklist (`:83-103`).
- Teste: comandos compostos com prefixo read-only → bloqueados; comandos read-only simples continuam passando; `rm -fr /`, `find ... -delete` continuam bloqueados.

Aceite da fase:
```bash
rg -n "passed=True" safety/ -A2   # nenhum dentro de except
.venv/bin/python -m pytest tests -m unit -q
scripts/ci-local.sh
```
Teste E2E V1 com ação perigosa bloqueado; teste de regressão TNR verde.

---

## FASE 3 — Ligar ou rotular os fios cortados

### 3.1 MCP `diagnose_incident` quebrado (2 bugs)
- `interfaces/http/app.py:140-149`: `create_app` chama `mount_sse` SEM engine/retriever → tools respondem `engine_unavailable`/`faiss_unavailable`.
- `interfaces/mcp/tools.py:672`: `engine.run(normalized_alert)` com 1 arg vs assinatura `run(pipeline, state)` (`blueprint/engine.py:58`) → TypeError.
- Fix: wiring completo no `create_app` (engine + pipeline + retriever via builders) e chamada correta montando `IncidentState` a partir do `NormalizedAlert`.
- Teste de integração: chamar a tool `diagnose_incident` via MCP → retorna diagnóstico (modo `--no-llm` equivalente).

### 3.2 A2A bloqueia o event loop
- `interfaces/http/routes/a2a.py:261`: `engine.run()` síncrono dentro de rota async (até `pipeline_timeout_seconds=90`).
- Fix: `await asyncio.to_thread(engine.run, ...)`. Teste: request concorrente de health responde enquanto diagnóstico roda.

### 3.3 Webhook → pipeline é stub silencioso
- `interfaces/http/webhook/pipeline_runner.py:143-168`: `_execute_pipeline` só loga `pipeline.stub_executed`; `:170-181` `_queue_worker` só dorme 3600s; `infra/run_webhook_server.py:72` cria runner SEM store. O provisioning do Grafana aponta para esse endpoint (`infra/grafana/provisioning/alerting/contact-points.yaml:11-14`) → a demo E2E Grafana→Slack NÃO funciona.
- Decisão: implementar de verdade (reusar BlueprintEngine V1 + builders; runner consome a fila e executa; store persistente) OU rotular explicitamente como stub (README + resposta do webhook + log level warning). PREFIRA implementar — o V1 já existe.
- Corrigir também `tests/integration/test_webhook_to_slack_flow.py` que referencia diretório `docker/` inexistente (virou `infra/`).
- Teste de integração: POST webhook Grafana válido → pipeline executa → postmortem gerado → Slack mock recebe mensagem.

### 3.4 Demo sem auth + Slack real por acidente
- `interfaces/demo/server.py`: ZERO auth em todos os endpoints, incluindo `POST /demo/start/{scenario}`; dispara Slack real se `SLACK_BOT_TOKEN` presente (`:58-70`).
- Fix: token simples (env `AIOPS_DEMO_TOKEN`, header) nos endpoints de mutação; Slack real só com opt-in explícito (`AIOPS_DEMO_SLACK_REAL=1`), default = mock/dry-run.

### 3.5 Rotas órfãs
- `interfaces/http/routes/knowledge.py` e `routes/admin.py` nunca são montadas; `get_knowledge_store` lança NotImplementedError (`knowledge.py:49-51`). Decisão: montar com implementação mínima OU deletar. Não deixe código morto com rota fantasma.

Aceite da fase: teste de integração webhook→Slack mock verde; tool MCP diagnose funcional em teste; smoke V0/V1 inalterados.

---

## FASE 4 — Segurança antes de exposição de rede

(Pode rodar em paralelo às Fases 2–3; não toca o pipeline.)

### 4.1 PKCE real
- `auth/server.py:107-112,175-181`: verifier e challenge na MESMA request; `pop_challenge` nunca chamado em produção. Fix: `/authorize` recebe só challenge (S256), armazena vinculado ao code; `/token` exige verifier e valida via `auth/pkce.py`. Trocar `==` por `hmac.compare_digest` em `auth/pkce.py:47`.
- Teste: fluxo completo authorize→token com verifier correto passa; verifier errado/ausente falha; challenge na request de token é rejeitado.

### 4.2 Autenticação de cliente
- `auth/server.py:238`: `token_endpoint_auth_methods_supported=["none"]`; modo `header` é o DEFAULT (`config/loader.py:205`) e autentica só por `X-Client-Id` sem segredo (`interfaces/mcp/sse.py:143-151`).
- Fix: segredo por cliente no modo header (env/config, comparação constant-time) OU mudar default para `oauth21`. Documentar a mudança no README/.env.example.

### 4.3 JWT com iss/aud + decisão RS256
- `auth/jwt_validator.py:60-66`: sem `iss`/`aud` → token MCP vale no A2A. Fix: emitir e validar `iss` + `aud` por audiência (MCP ≠ A2A).
- `auth/server.py:186-191,241-244`: RS256 anunciado + JWKS publicado mas `issue_token` sempre HS256; RSA configurado + secret curto → InvalidKeyError → 500. Decisão: implementar RS256 de verdade OU remover do metadata/JWKS. Não anuncie o que não implementa.
- `auth/oidc_validator.py:176-178`: kid não encontrado → usa primeira chave do JWKS; `RSAAlgorithm.from_jwk` mesmo para ES256. Fix: kid desconhecido → rejeitar (após refresh único do JWKS); selecionar algoritmo pelo `kty`/`alg` da JWK.

### 4.4 SQL parametrizado no Databricks
- `tools/databricks_connector.py:277-282` (`safe_query=query.replace("'","''")` interpolado em LIKE), `:302` (LIMIT), `:449-463` (metrics), `:627-648` (traces): SQL por f-string. A API Databricks suporta parâmetros nomeados — use-os em TODOS os queries.
- Teste: input com aspas/SQL injection → query parametrizada, sem erro e sem escape manual.

### 4.5 Higiene de chaves e sessão
- `auth/keys.py:100-104`: chave privada sem `chmod 0600` — aplicar na escrita.
- `interfaces/mcp/sse.py:217-246`: `POST /mcp/messages` não vincula `session_id` ao cliente autenticado — vincular sessão→client_id na criação e validar no POST.

Aceite da fase:
```bash
rg -n "replace\(\"'\"" tools/databricks_connector.py   # vazio
rg -n "f\".*(SELECT|LIKE|LIMIT)" tools/databricks_connector.py  # vazio
.venv/bin/python -m pytest tests -m unit -q
```
Testes de fluxo PKCE, cross-audience rejeitado, kid desconhecido rejeitado.

---

## FASE 5 — Convergência arquitetural (aposentar o V0)

PRÉ-REQUISITO: Fases 2 e 3 concluídas (V1 com safety e RAG reais). Executar as fases restantes de `docs/refactoring-execution-plan.md` (2b LLM seam, 2c Reporter, 2d ports/persistence, 3 domain purity, 5 builders) — leia esse arquivo e siga os acceptance criteria dele — MAIS os itens abaixo:

### 5.1 Quebrar o god-file `main.py` (2247 linhas)
- ~15 funções `step_*` misturam lógica de negócio + Rich UI (`step_generate_data:138`, `step_sanitize:179`, `step_pipeline:223`, `step_compression:272`, `step_rag:318`, `step_agents:413`, `step_safety_validation:647`, `step_remediation_validation:717`, `step_predict:951`, `step_postmortem:1019`, `step_tracking:1508`).
- Destino: lógica → use cases em `application/`; apresentação Rich → adapter Reporter (`adapters/reporter/rich_console.py` já existe); CLI fina → `interfaces/cli/`.
- Remover duplicações: mapa de confiança `{"high":0.85,"medium":0.5,"low":0.25}` 2× (`main.py:2118,2196`); state machine reimplementada inline em `step_agents` (comentário `main.py:492`) enquanto `AgentOrchestrator.run` também a tem; chamada dupla `detector.detect` (`agents/orchestrator.py:264-265` vs `main.py:507`); acesso a privado `diagnoser._heuristic_diagnosis` (`main.py:528`) → expor método público; `_run_v0` morto (`main.py:1944-1953`) → deletar.

### 5.2 Uma única composition root
- `app/builders.py` para TODOS os entrypoints (CLI V1, demo, A2A, MCP, webhook). V0 permanece até 5.4.
- Parser manual de `.env.local` duplicado 4× (`main.py:2008-2015`, `main.py:1782-1788`, `infra/run_webhook_server.py:20-26`, `interfaces/demo/server.py:44-51`) → pydantic-settings/dotenv via loader único (`config/loader.py`).
- Unificar os dois tipos `MitigationAction` (`domain.incident` vs `safety.tnr`) — domain é a fonte; safety converte na borda.

### 5.3 Config drift
- `slack.webhook_url` fantasma (`config/defaults.yaml:34`, não existe em `SlackConfig` `config/loader.py:84-119`, `extra="ignore"` esconde) → remover ou implementar.
- `webhook.dedup_window_seconds`/`max_payload_bytes` lidos via getattr fallback (ausentes de `WebhookConfig` `config/loader.py:74-81`) → adicionar ao modelo.
- `tools/slack_setup.py:34-37` ignora `config.slack` (max_retries/backoff/fallback_dir/dry_run) → usar config.
- `.env.example`: adicionar `JIRA_URL/PROJECT_KEY/EMAIL/API_TOKEN` (`tools/jira_tracker.py:72-75`), `SLACK_API_BASE_URL`, `AIOPS_MCP__OAUTH__JWT_SECRET`.

### 5.4 Critério de remoção do V0
- Rodar golden validation (`validation/runner.py`) e suíte de snapshots nos DOIS engines com os mesmos cenários → outputs equivalentes (diferenças justificadas e documentadas).
- Só então: deletar `step_*`, `_run_v0`, factory `get_pipeline` (`main.py:1923-1941`, `core/pipeline_protocol.py:16-64`) e a flag `--pipeline-v1` (V1 vira o único).
- `main.py` final < 300 linhas (CLI + wiring).

Aceite da fase:
```bash
wc -l main.py                                  # < 300
rg -n "\.env\.local" --type py                 # só no loader
rg -n "from anthropic|import anthropic" -g "*.py"  # só adapters/llm/anthropic_client.py
scripts/ci-local.sh                            # completo verde
```

---

## FASE 6 — Paridade Rust/Python + features mortas

### 6.1 Divergências silenciosas Rust↔Python
- `pipeline/metric_filter.py:48`: Rust só é usado se `sigma_threshold == 3.0 and baseline_ratio == 0.5`; `pipeline/trace_filter.py:48`: só se `percentile_threshold == 95.0 and max_hops == 3` → config custom cai no Python SEM AVISO, com algoritmos diferentes. Fix: emitir warning estruturado explícito ao cair no fallback por config (ou implementar os parâmetros no Rust).
- `pipeline/telemetry_sanitizer.py:505` (`_detect_taint`): path Rust IGNORA `custom_patterns` do construtor → passar os patterns ao Rust ou forçar fallback Python quando houver custom patterns (com warning).
- `pipeline/log_parser.py`: fallback Python amostra no máx. 500 logs (`logs[::step]`), Rust processa todos; `get_error_templates()` chama `self.parse([])` — com Rust retorna `[]` (sem estado acumulado), com Python retorna templates acumulados. Unificar a semântica (estado acumulado em ambos ou em nenhum) e documentar.
- Testes de paridade: mesmos inputs → mesmos outputs (ou diferenças documentadas e aceitáveis) nos pares Rust/Python.

### 6.2 Decisão explícita por feature morta (wire ou delete)
- Reranker nunca é wired em produção (`main.py:373` cria retriever sem reranker); Tier 2 cross-encoder nunca ativa (nenhum caller passa `use_tier2`).
- `CoTObservabilityRetriever`/`DomainReranker` nunca instanciados fora de testes; `rag/cot_retriever.py:53` chama `self.reranker.rerank(query, raw)` vs assinatura `rerank(results, query, top_k)` → AttributeError ao ativar; há duplo rerank (search já reranqueia).
- Para CADA um: ou wire com teste de integração, ou delete com nota no CHANGELOG. Não deixe código morto com bug latente.

### 6.3 Caches e reentrância
- `rag/retriever.py:14,76`: `_query_cache` keyed só por `md5(query)`; `_embedding_cache` por texto → trocar modelo de embedding retorna vetores stale; crescimento ilimitado. Fix: incluir `model_name` na chave + limite LRU.
- `blueprint/engine.py:79`: `BlueprintEngine.results` resetado por run → não reentrante. Fix: results por run (retornado/local), não por instância.
- `blueprint/engine.py:49-56`: `increment_llm_calls` (limite default 15) nunca é chamado pelo engine → wire no node agentic ou remover.
- `blueprint/engine.py:84-90`: timeout checado só entre nodes — documentar limitação ou implementar por-node.
- `core/lifecycle.py:37`: `LifecycleObserver`/`ALLOWED_EVENT_TYPES` definidos mas o engine não emite eventos → wire ou remover.

### 6.4 Testes Rust nativos
- Só 2 de 7 módulos têm testes (`rust/src/cusum_ewma/analyzer.rs` 7, `rust/src/trace_predictor/predictor.rs` 10). Adicionar testes unitários para `context_compressor`, `log_parser`, `metric_filter`, `telemetry_sanitizer`, `trace_filter`.
- Adicionar `cargo clippy -- -D warnings` e `cargo fmt --check` ao job rust do CI.

### 6.5 Code smells menores (opportunistic, dentro dos arquivos já tocados)
- `agents/orchestrator.py:443`: `DiagnosisAgent()` recriado a cada step (reseta estado/cliente) → reusar instância.
- `agents/orchestrator.py:390,473,504`: `json.loads(response.content[0].text)` sem tolerar markdown code-fence → helper de extração tolerante com erro explícito (não fallback silencioso).
- `pipeline/trace_filter.py:99-107`: DFS de call path computado e descartado; usa `trace_spans[:max_hops]` por `start_time` (ordem temporal ≠ causal) → usar o DFS ou remover o cálculo.
- `pipeline/trace_filter.py:90`: `np.mean([])` → `nan` quando durações iguais → guardar.
- `agents/skill_evolver.py` (~linha 90): modelo haiku hardcoded → config.
- `main.py:625-637` e `rag/indexer.py`: `except Exception: pass` em evolução de skills/carga histórica → logar erro estruturado.
- Demos (`demo_adversarial_attack`) dentro de módulos de produção → mover para `interfaces/demo/` ou `scripts/`.
- `rag/memory.py`: similaridade de incidentes com full table scan — aceitável no PoC; documentar como limitação conhecida (não otimizar agora).

Aceite da fase: `cargo test` + clippy verdes; testes de paridade verdes; `rg` confirma decisões (features wired ou deletadas); CI completo verde.

---

## Definition of Done (global)

- [ ] Todas as fases concluídas ou explicitamente marcadas skipped/blocked com motivo em `docs/improvement-progress.md`.
- [ ] CI completo verde (unit + integration/snapshot + rust + lint).
- [ ] Smoke: `main.py --no-llm --no-rag` e `--pipeline-v1` funcionam; exit codes 0/2/3 preservados.
- [ ] Nenhum `passed=True` em caminho de exceção na safety; nenhuma ação executada em infra real (contrato do SECURITY.md intacto).
- [ ] README e docs atualizados onde o comportamento mudou (webhook, MCP, demo auth, modo header, RS256).
- [ ] Relatório final: por fase — o que mudou, testes adicionados, diffs de snapshot justificados, o que ficou pendente e por quê.

Comece pela Fase 0. Não pule fases sem registrar o motivo. Em caso de ambiguidade de produto (ex.: 3.3 implementar vs rotular, 6.2 wire vs delete), escolha a opção que torna o sistema mais honesto sobre o que realmente faz, registre a decisão, e siga.
