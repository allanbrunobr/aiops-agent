# Progresso das correções P2

- **Origem:** §5 "P2 — próximo ciclo" de [`analise-20261109.md`](analise-20261109.md).
- **Branch:** `p2-correcoes`, local, criada a partir de `10370ce` (a ponta
  das correções P1 em `analise-aiops`). Sem push e sem PR.
- **Método:** o mesmo do P1, descrito em [`p1-progresso.md`](p1-progresso.md):
  teste falhando antes, um commit por item e, depois de cada item, `ruff check
  .` e os três jobs de pytest do CI com status 0.
- **Ordem:** gravidade do achado.

## Item P2-1 — MCP funcional (ARQ-02, 4B-02, 4B-03, SEC-08, T-b)

**Defeitos:**

- `diagnose_incident` chamava `engine.run(alert)`, mas a assinatura real é
  `run(pipeline, state)`. Toda chamada devolvia 500.
- O `create_app` montava o SSE **antes** de criar a engine, então o MCP nunca
  a recebia (devolvia 503).
- O `TelemetrySanitizer` não tinha `sanitize_text`. Por isso o
  `validate_action` reprovava qualquer comando, e a busca devolvia os campos
  redigidos por falha (o fail-closed do P1).
- Os testes antigos usavam dublês com essa mesma API inexistente, e por isso
  passavam verdes.

**Correção:**

- `pipeline/telemetry_sanitizer.py`: `sanitize_text(text)` aplica a texto solto
  as mesmas regras do campo `message` dos logs: neutraliza payload
  adversarial, redige email, IP e URL e, em modo estrito, abstrai conteúdo
  user-generated.
- `interfaces/mcp/tools.py`, `handle_diagnose_incident`:
  - monta um `IncidentState` e chama `engine.run(build_pipeline(config),
    state)`;
  - lê da resposta os campos que o estado tem: `diagnosis`, `proposed_action`,
    `safety_verdict` e `safety_reasons`;
  - usa LLM só com `ANTHROPIC_API_KEY`, a mesma regra da CLI;
  - num bloqueio de safety, responde `type: "safety_blocked"` (409) com o
    diagnóstico, a camada e o motivo.
- `interfaces/http/app.py`: engine e retriever são criados antes do
  `mount_sse`, repassados ao MCP e reusados em `app.state`.
- `tests/test_diagnose_incident.py`: os fakes passaram a ter o contrato real,
  `run(self, pipeline, state)`. O teste de rastreio agora também confere que o
  estado carrega o alerta normalizado.

**Testes:** `tests/test_mcp_contract.py`, 9 testes marcados `unit`, usando as
classes **reais**.

- **Antes da correção:** 8 falharam. O que passava (comando com PII
  bloqueado) passava porque, até então, **todo** comando era bloqueado.
- **Cenários cobertos:**
  - `sanitize_text` com texto limpo, com PII e com payload adversarial;
  - `validate_action` com as 4 camadas reais (comando limpo passa, comando
    com PII é bloqueado);
  - busca com o sanitizer real: redige a PII e mantém o resto do texto;
  - `diagnose_incident` com a `BlueprintEngine` real e com um bloqueio de
    safety;
  - `create_app` repassando engine e retriever ao `mount_sse`.

**CI local:** ruff limpo. unit 839 passaram, integration 138 passaram, suíte
completa 1530 passaram e 10 foram pulados. Status 0 nos três.

**Limite que continua:** a MCP e a A2A rodam o pipeline **sem telemetria**.
O estado nasce só com o alerta, sem logs, métricas ou traces, e o diagnóstico
sai da heurística sobre nada. Ligar uma fonte de telemetria a essas entradas é
trabalho de arquitetura (relatório: ARQ-10 e o stub do webhook), não deste
item.

## Item P2-2 — Cache do índice RAG devolvia o chunk errado (B4/F06)

**Defeito.** O FAISS guarda só textos únicos, e os chunks têm duplicatas: o
texto de métrica omite o serviço. O `build_index` criava `_faiss_to_chunks`,
mas o `load_index` não o recriava, e o retriever caía em `chunks[idx]`.

- **Reproduzido:** buscando o texto exato de um trace no índice carregado do
  cache, voltava um chunk de **métrica**. O LLM recebia evidência de outra
  fonte e de outro serviço.

**Correção** (`rag/indexer.py`):

- `_build_faiss_to_chunks()` recalcula o mapeamento a partir dos chunks, na
  mesma ordem do `build_index` (`dict.fromkeys`). O `build_index` e o
  `load_index` usam a mesma função.
- O `load_index` confere `len(mapeamento) == index.ntotal`. Se divergir, o
  cache é tratado como inválido e o índice é reconstruído.

**Testes:** `tests/test_rag_cache_mapping.py`, 3 testes marcados `unit`.

- **Ambiente:** modelo de embedding determinístico e um FAISS mínimo em numpy.
  O projeto proíbe carregar ML nos testes; a primeira versão, com FAISS real,
  quebrou o `test_smoke::TestMLIsolation` no job completo e foi trocada.
- **Antes da correção:** os 3 falharam, inclusive com o FAISS falso, conferido
  pondo o `indexer.py` antigo de volta.
- **Cenários cobertos:**
  - índice novo e índice do cache devolvem o mesmo chunk;
  - toda consulta concorda entre os dois;
  - divergência de tamanho invalida o cache.
- **Ajuste em `tests/test_rag_persistence.py`:** o `read_index` falso passou a
  ter `ntotal` coerente com os chunks gravados, como um índice real.

**CI local:** ruff limpo. unit 842 passaram, integration 138 passaram, suíte
completa 1533 passaram e 10 foram pulados. Status 0 nos três.

## Item P2-3 — Engine síncrona travava o event loop (B6/4B-04)

**Defeito.** A rota A2A (`_handle_diagnose`) chamava `engine.run`, que é
síncrono e faz chamadas de LLM, direto dentro do handler `async`. Enquanto o
pipeline rodava, o processo inteiro parava: `/health`, outras tasks e o MCP.
Depois do P2-1, o `diagnose_incident` do MCP também chamava a engine, com o
mesmo defeito.

- **Medido:** com a engine "trabalhando" 0,4 s, uma corrotina vizinha
  avançou **1 tick** de 20 ms. Com o loop livre, seriam cerca de 20.

**Correção:** `await asyncio.to_thread(engine.run, pipeline, state)` em
`interfaces/http/routes/a2a.py` e em `interfaces/mcp/tools.py`.

**Testes:** `tests/test_engine_off_event_loop.py`, 2 testes marcados `unit`
(A2A pela rota HTTP real e MCP pelo handler).

- **Antes da correção:** os 2 falharam com 1 tick.
- **Depois:** passam com folga. O limite do teste é 5 ticks ou mais.

**CI local:** ruff limpo e os três jobs com status 0 (ver o commit).

**Risco que continua (baixo):** a instância da `BlueprintEngine` é
compartilhada pelo `app.state`, e o `run()` reatribui `self.results`. Com
execuções concorrentes em threads, o log `pipeline.completed all_success`
pode refletir outra execução. Nada em produção lê `engine.results` (conferido
com grep). Isolar exigiria uma engine por requisição ou resultados locais ao
`run()`.

## Item P2-4 — Modo databricks-live coletava zero dados sem avisar (B8/4B-05)

**Defeito.** A etapa `main.step_fetch_databricks_data` chamava os conectores
com valores que esvaziavam tudo, sem erro:

- **Métricas:** `query(start=None, end=None)` fazia `None.isoformat()`, e a
  exceção era engolida como "fetch failed".
- **Logs:** `search(query="*")` virava `LIKE '%*%'`, um asterisco literal.
- **Traces:** `find_traces(service="")` virava `WHERE service = ''`.

**Correção:**

- `tools/databricks_connector.py`:
  - `"*"` ou vazio não vira filtro de texto;
  - `service` vazio não vira filtro;
  - `start`, `end` e `promql` são opcionais nas métricas;
  - o `WHERE` só é montado quando há cláusulas. Sem isso, a consulta ficaria
    com um `WHERE` vazio.
- `main.py`:
  - as três fontes recebem uma janela explícita de
    `DATABRICKS_LOOKBACK_MINUTES = 60`;
  - quando as três voltam vazias, aparece um aviso em vermelho no console e
    um `databricks.no_telemetry` no log.

**Decisão:** o "fallback gracioso", que devolve o incidente mesmo sem dados, é
contrato deliberado, fixado em `tests/test_cli_data_source.py`. Foi mantido. O
que mudou é que esse caso deixa de ser silencioso.

**Testes:** `tests/test_databricks_live_queries.py`, 6 testes marcados `unit`.
Host e token são fictícios e `_execute_query` é substituído, então nada sai
para a rede.

- **Antes da correção:** 5 falharam. O que passava é a guarda de que a
  janela, quando informada, continua sendo aplicada.

**CI local:** ruff limpo e os três jobs com status 0 (ver o commit).

**Para o Allan:** os 60 minutos de janela são um padrão razoável, não uma
medição do volume real das tabelas. Ajuste conforme o uso.

## Item P2-5 — Router do webhook: testes e três defeitos (T-c, 4B-01, 4B-08, 4B-09/SEC-10)

**Ponto de partida.** A porta de entrada externa não tinha nenhum teste, e o
arquivo não era importado por nenhum teste. Os testes novos acharam três
defeitos:

- **4B-01:** o fingerprint de dedup era gravado **antes** do submit. Com a
  fila cheia (503), o reenvio do emissor era respondido como `deduplicated`, e
  o alerta se perdia.
- **4B-08:** um payload autenticado com tipo inesperado (`alerts: [1]`) gerava
  500, porque só `ValueError` e `KeyError` eram tratados, e o emissor
  reenviava.
- **4B-09/SEC-10:** sem `Content-Length` (envio chunked), o corpo era lido
  **inteiro** antes de checar o limite. Medido: 64 de 64 pedaços de 64 KiB,
  ou seja, 4 MiB para um limite de 1 MiB.

**Correção** (`interfaces/http/webhook/router.py`):

- `_read_body_limited` lê `request.stream()` em pedaços e para assim que passa
  do limite (413).
- `TypeError` e `AttributeError` do normalizador viram 422.
- O dedup **consulta** antes do submit e **registra** só depois que o pipeline
  aceita o alerta.

**Testes:** `tests/test_webhook_router.py`, 9 testes marcados `unit`, com o
router real.

- **Antes da correção:** 3 falharam, exatamente os três defeitos. Os outros 6
  cobrem comportamento que já estava correto e não tinha teste:
  - 202 com submit;
  - 401 sem token e com token errado;
  - 413 declarado;
  - 422 e 400 para JSON inválido e não-objeto;
  - duplicata depois do sucesso.
- **Instrumento da leitura limitada:** chamada ASGI direta com um `receive` que
  conta os pedaços consumidos. O `TestClient` bufferiza o corpo inteiro e não
  distinguiria o defeito.

**CI local:** ruff limpo e os três jobs com status 0 (ver o commit).

**Troca consciente:** como o fingerprint só é gravado depois do aceite, dois
envios idênticos **simultâneos** podem ambos passar pela consulta e ambos ser
submetidos. Antes, a janela era menor, mas a perda de alerta no 503 era
garantida. Fechar essa janela pede uma reserva atômica no `DedupStore`.

## PR #20 aberto e achados da revisão pré-PR

**PR:** https://github.com/allanbrunobr/aiops-agent/pull/20, de
`analise-aiops` (`10370ce`, 17 commits) para `main`.

- O push foi feito pelo coordenador, fora do Claude, sem force.
- O PR foi aberto com `gh`, na conta `allanbrunobr`.
- Nenhum commit da `p2-correcoes` entrou nele.

**A revisão `pre-pr-contradicoes` deixou dois achados de pé.** Como não houve
novo push, eles foram registrados no corpo do PR como conhecidos e são
corrigidos aqui:

1. **Testes de `main()` sem isolamento** (`tests/test_api_key_safety.py`).
   Corrigido abaixo.
2. **Frases desatualizadas no `docs/p1-progresso.md`.** Corrigido no commit
   seguinte.

## Item P2-6 — Isolamento automático de efeitos externos nos testes

**Defeito.** A fixture `isolated_cli` era opcional. Os testes de `main()` em
`tests/test_api_key_safety.py` (`TestPipelineOutputNoApiKeys`,
`TestMissingApiKeyFallback`) não a pediam. Num checkout com `.env.local`
contendo `SLACK_BOT_TOKEN`, eles montavam um `SlackNotifier` real e podiam
postar no canal.

**Correção:** uma fixture `autouse` no `tests/conftest.py` aplica o
`isolated_cli` a **todo** teste, em vez de depender de cada teste pedir.

**Teste:** `tests/test_cli_isolation.py::test_slack_is_disabled_even_without_requesting_the_fixture`.

- **Antes da correção:** falhou. Com `SLACK_BOT_TOKEN` no ambiente e sem pedir
  a fixture, o teste recebia um `SlackNotifier` real.
- **Instrumento:** o teste não cria nenhum `.env.local`, porque segredos e
  `.env` ficam fora de alcance. Ele usa `monkeypatch.setenv` com um token
  gerado na hora.

**CI local:** ruff limpo. unit 860 passaram, integration 138 passaram, suíte
completa 1551 passaram e 10 foram pulados. Status 0 nos três.

## Correção de documentação — `docs/p1-progresso.md` desatualizado (achado da revisão pré-PR)

O documento ainda dizia três coisas falsas no `10370ce`:

- que o CI não tem lint;
- que nada tinha sido enviado ao remoto;
- que o ruff no CI, o push e o PR eram pendências.

Também listava como pendentes os segredos (já gerados) e o `aud`/`iss` (já
configurável).

**O que mudou:**

- **Topo do documento:** passa a apontar o PR #20.
- **"Como verde é medido":** inclui o job de lint, deixando claro que os itens
  anteriores ao `10370ce` foram verificados só com pytest.
- **Pendências:** a tabela mostra o que foi feito e o que realmente falta
  (entregar os segredos aos clientes e escolher os valores de
  `issuer`/`audience`).

**Teste:** não há, porque é só texto. A conferência foi um `grep` pelas frases
antigas, que não acha mais nenhuma, e a checagem das referências
`arquivo:linha` do documento.

## Item P2-7 — Todo teste com marcador de nível (T-a)

**Defeito.** Havia **542 testes** (39% da suíte, em 40 arquivos) sem nenhum
marcador `unit`, `integration` ou `snapshot`. Eles só rodavam no job
`rust-build`, depois de `cargo`, `maturin` e torch, e sempre com a extensão
Rust presente. Entre eles estavam testes de segurança (`test_oauth_mcp`,
`test_safety_audit`, `test_story_1_7_safety_nodes`). Um teste novo sem
marcador ficava fora dos jobs rápidos sem nenhum aviso.

**Correção:**

- **Portão na coleta** (`tests/conftest.py`): `pytest_collection_modifyitems`
  com `tryfirst`, para ver a suíte inteira antes da desseleção por `-m`.
  Falha com `UsageError` listando os testes sem um dos níveis
  `unit | integration | snapshot | rust`.
- **Novo marcador `rust`** (`pyproject.toml`): testes que exigem a extensão
  `_aiops_rust` e rodam só no job `rust-build`.
- **Marcação** (com um script de AST):
  - 38 arquivos inteiramente sem marcador recebem `pytestmark`: 35 `unit`,
    2 `rust` e 1 `integration` (`tests/integration/test_webhook_to_slack_flow.py`);
  - nos 2 arquivos mistos, os testes que faltavam foram marcados
    individualmente.
- **Critério de nível, medido e não suposto:** marquei como `unit` e rodei o
  job unit, que só tem as dependências mínimas. Todos passaram, então nenhum
  precisou virar `integration`.

**Testes:** `tests/test_tier_marker_gate.py`, 6 testes marcados `unit`
(itens sem nível são acusados, cada nível satisfaz o portão, a sessão inteira
está marcada).

- **Antes da marcação:** com o portão ligado, a coleta da suíte inteira
  **reprovou listando 543 testes**.
- **Erros no caminho, corrigidos antes do commit:**
  - em 8 arquivos o `import pytest` vinha depois de outro código e o
    `pytestmark` ficou antes dele (`NameError`), então foi reposicionado;
  - a função auxiliar do portão se chamava `tests_without_tier` e foi
    coletada como teste, então foi renomeada para `untiered_items`.

**Efeito medido:** o job unit, o gate rápido, passou de **860 para 1379
testes**. Antes de cada commit: ruff limpo e os três jobs com status 0
(unit 1379, integration 138 com 5 pulados, suíte completa 1557 com 10
pulados).

## Item P2-8 — `mcp.auth_method` desconhecido falha fechado (S4/SEC-03)

**Defeito.** `auth_method` era string livre. Qualquer valor fora de `oauth21`
e `header` caía em `anonymous`, sem checagem de scope. Um erro de digitação
como `oauth2.1` deixava o MCP aberto. O modo `federated` tem caminho próprio
nos dois endpoints e não era afetado.

**Correção:**

- `config/loader.py`: `auth_method: Literal["header", "oauth21", "federated",
  "none"]`. Um valor inválido derruba a carga da config, ou seja, o startup
  falha com mensagem.
- `interfaces/mcp/sse.py`: `none` passa a ser tratado de forma explícita, e
  qualquer outro valor devolve 401. É defesa em profundidade para configs que
  não passam pelo `AppConfig`.

**Testes:** `tests/test_mcp_auth_method.py`, 10 testes marcados `unit`.

- **Antes da correção:** 5 falharam. Os 4 valores inválidos eram aceitos na
  config, e o método desconhecido passava pela autenticação do SSE.
- **Instrumento:** um transporte SSE falso, que levanta uma exceção sentinela
  quando a autenticação deixa o pedido passar.

**CI local:** ruff limpo e os três jobs com status 0 (ver o commit).

**Decisão do Allan, não alterada:**

- O relatório recomenda tirar `header` do padrão, porque o `X-Client-Id`
  pode ser forjado. Mudar o padrão para `oauth21` exige que todos os clientes
  passem a usar token, com os segredos que hoje existem só na máquina do
  Allan.

**Achado de passagem, não corrigido:**

- O `POST /mcp/messages` deixa o transporte do MCP responder e, em seguida,
  o FastAPI tenta responder de novo. O `TestClient` acusa "multiple
  http.response.start". Vale conferir com o padrão de montagem (`Mount`)
  recomendado pelo SDK do MCP.

## Item P2-9 — A configuração de safety é respeitada (S7/F13)

**Defeito.** O `config/defaults.yaml` declara `safety.require_human_approval:
true` ("Sempre requer aprovação humana para ações") e `blast_radius_limit: 5`.
Mesmo assim, a CLI fixava `auto_approve_low_risk=True` e
`max_blast_radius=3`. O node V1 usava o limite da config, mas também ignorava
`require_human_approval`.

**Correção:**

- `main.step_safety_validation(result, config=...)` lê `config.safety`. O
  `main()` passa a config carregada.
- `blueprint/adapters/safety_nodes.py`: o `pre_validator` usa
  `auto_approve_low_risk = not require_human_approval`.

**Mudança de comportamento:** com a config padrão, nenhuma ação sai
auto-aprovada, e a CLI (V0 e V1) termina com **exit 3** sempre que houver
mitigação proposta. É o que o YAML declara. Para voltar a auto-aprovar ações de
baixo risco, use `safety.require_human_approval: false`.

**Testes:** `tests/test_safety_config_respected.py`, 5 testes marcados `unit`.

- **Antes da correção:** 4 falharam.
  - O do V1 falhou pelo motivo real: devolvia `APPROVED` com
    `require_human_approval=true`.
  - Os 3 do V0 falharam por `TypeError`, porque a função não recebia a config.
    Nesses, o defeito (valores fixos no código) foi confirmado por leitura, não
    pelo vermelho do teste.
- **Ajuste em `tests/test_cli_safety_decision.py`:** o teste do caso "o
  pre-validator aprova e o VersionValidator bloqueia" passou a usar uma config
  com `require_human_approval: false`. Com o padrão, o pre-validator nunca
  aprova e o cenário deixaria de existir.

**CI local:** ruff limpo. unit 1394 passaram, integration 138 passaram, suíte
completa 1572 passaram e 10 foram pulados. Status 0 nos três.
