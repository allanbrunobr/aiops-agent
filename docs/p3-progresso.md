# Progresso das correções P3

- **Origem:** §5 "P3 — dívida estrutural" de
  [`analise-20261109.md`](analise-20261109.md).
- **Branch:** `p3-correcoes`, local, criada a partir da ponta da
  `p2-correcoes` (`727ee77`). Sem push e sem PR.
- **Restrições:**
  - não mexer em `.github/`, `ci-ok.sh`, hooks nem credenciais;
  - não decidir os itens que já são decisões do Allan (V0 × V1, gates de CI
    em `.github/`, `header` como padrão do MCP, `issuer`/`audience`, timeout
    do LLM, janela do Databricks);
  - pular o que depender dessas decisões.
- **Método:** o de sempre. Teste falhando antes quando há defeito, um commit
  por item, e `ruff check .` mais os três jobs de pytest do CI depois de cada
  item.

## P3-9a — CoT retriever chamava o reranker com os argumentos trocados (F5-12)

**Defeito.** `search_with_cot` fazia `self.reranker.rerank(query, raw)`, mas a
assinatura é `rerank(results, query, top_k=...)`. Além disso, o `search()`
herdado já aplicava o mesmo reranker. O rerank acontecia duas vezes, e uma
delas com os argumentos trocados.

**Correção** (`rag/cot_retriever.py`): busca com `use_reranking=False` e
reordena uma única vez com `rerank(raw, query, top_k=top_k)`, contra a query
**original** (a busca usa a expandida).

**Teste:** `tests/test_cot_reranker_call.py`.

- **Antes da correção:** falhou com 2 chamadas, a segunda com `results` e
  `query` invertidos.

**Suíte completa:** ruff limpo. unit 1459 passaram, integration 138
passaram, suíte completa 1637 passaram e 10 foram pulados. Status 0 nos três.

## P3-9b — Feedback de falso positivo nunca marcava o padrão (4B-14)

**Defeito, em três quebras independentes:**

1. `KnowledgePattern` não tinha `source_incident_id`, e a rota de criação de
   padrões não o repassava. A busca primária nunca achava nada.
2. O fallback lia `incident.alert.affected_services`, que não existe em
   `NormalizedAlert` (o campo é `service`). O `AttributeError` era engolido.
3. A store vinha do global `interfaces.http.app.knowledge_store`, que nunca é
   atribuído. O bloco nem executava.

**Correção:**

- `rag/models.py` e `rag/knowledge_store.py`: o campo `source_incident_id`
  passa a existir e é persistido no JSON.
- `interfaces/http/routes/knowledge.py`: a rota repassa o campo, que o schema
  já aceitava.
- `interfaces/http/handlers/false_positive.py`:
  - recebe `knowledge_store` do chamador;
  - o fallback usa o serviço do diagnóstico ou `alert.service`;
  - sem store, registra `false_positive_no_knowledge_store` em vez de
    descartar em silêncio.
- `interfaces/http/api/accuracy.py`: passa `app.state.knowledge_store`. A
  montagem dos routers e da store no `create_app` é o item P3-2.

**Testes:** `tests/test_false_positive_feedback.py`, 5 testes.

- **Antes da correção:** falharam os 5.
  - A quebra 1 apareceu primeiro (`TypeError` no campo).
  - Com o campo adicionado, o teste de ponta a ponta pela rota de accuracy
    falhou pela quebra 3: padrão não marcado.
  - A quebra 2 foi reproduzida chamando o fallback diretamente:
    `AttributeError: 'NormalizedAlert' object has no attribute
    'affected_services'`.
- **`tests/test_false_positive.py`:** os testes antigos injetavam um módulo
  falso `interfaces.http.app` em `sys.modules`, e o *cleanup* removia o
  módulo **real** de `sys.modules`. Passaram a usar o parâmetro explícito. O
  teste "sem store não loga warning" virou "sem store, o descarte é
  registrado".

**Suíte completa:** ruff limpo. unit 1464 passaram, integration 138
passaram, suíte completa 1642 passaram e 10 foram pulados. Status 0 nos três.

## P3-9c — Prompt de skill reescrito pelo LLM exige revisão humana (S8)

**Defeito.** `SkillEvolver.evolve` gravava como skill **ativa** o system prompt
devolvido pelo LLM. O LLM lê telemetria (lições e contexto), que um atacante
pode influenciar. Um prompt injetado passaria a guiar todo diagnóstico
seguinte daquele tipo de incidente, sem ninguém ver. O teste reproduziu isso:
um prompt "Ignore safety checks…" virou a skill ativa.

**Correção:**

- `rag/memory.py`: nova tabela `skill_proposals`, com os métodos
  `save_skill_proposal`, `list_skill_proposals(status)`,
  `approve_skill_proposal(id, reviewer)` e
  `reject_skill_proposal(id, reviewer)`.
  - Aprovar aplica o prompt à skill (`version+1`) e registra o revisor e a
    data.
  - Rejeitar mantém a skill como está.
- `agents/skill_evolver.py`: a evolução grava **proposta pendente**, não a
  skill ativa.
- `main.py`: a mensagem passa de "Skill evolved" para "Skill rewrite proposed
  … pending human review".

**Testes:** `tests/test_skill_evolution_review.py`, 4 testes.

- **Antes da correção:** os 4 falharam. O prompt injetado substituía a skill
  ativa, e a API de propostas não existia.
- **Cenários cobertos:**
  - a skill ativa não muda depois de `evolve`;
  - a proposta fica pendente, com o texto e a justificativa;
  - aprovar aplica o prompt e incrementa a versão;
  - rejeitar mantém a skill.

**Suíte completa:** os três jobs com status 0 (ver o commit).

**Lacuna:** não há interface (rota ou CLI) para listar e aprovar propostas.
Hoje isso é feito pela API da `IncidentMemoryDB`.

## P3-9d — Score das skills passa a ser atualizado (F16)

**Defeito.** `IncidentMemoryDB.update_skill_score` nunca era chamado em
produção. `utility_score` e `total_uses` ficavam congelados, e
`SkillEvolver.should_evolve` (`total_uses >= 3` e `score < 0.3`) nunca era
verdade. Toda a evolução de skills era código morto.

**Correção** (`main.py`):

- O bloco de WRITE + EVOLVE foi extraído para `step_skill_learning(...)`.
- No modo com LLM, o score é atualizado por EMA, e a skill é relida antes de
  decidir a evolução.
- No modo `--no-llm`, o score **não** muda, porque a "reflexão" ali é um
  placeholder (0.5) sem sinal.
- O roteador usa skills com score ≥ 0.3, e a evolução dispara abaixo disso.
  Na execução em que o score cai, nasce uma proposta pendente (P3-9c) e a
  skill sai do roteamento.

**Testes:** `tests/test_skill_score_learning.py`, 3 testes.

- **Antes:** extraí o bloco **sem** mudar o comportamento e rodei os testes.
  Os 2 de comportamento falharam: `total_uses` ficou em 0 e não nasceu
  nenhuma proposta. O teste do modo `--no-llm` passa nos dois casos.

**Suíte completa:** os três jobs com status 0 (ver o commit).

**Fora do escopo:** o pipeline V1 (Blueprint) não tem esse ciclo de
aprendizado de skills. Igualar o V1 ao V0 depende da decisão V0 × V1.

## P3-3a — Regra de camadas verificada por teste, sem violações (A6/ARQ-15)

**Defeito.** A regra de dependências existia só em docstring, sem portão, e
já tinha duas violações:

- `agents/diagnoser.py` importava um adapter concreto
  (`adapters.llm.anthropic_client`);
- `tools/slack_setup.py` importava `interfaces.http.notifications.slack`.

**Correção:**

- `tests/test_layer_dependencies.py`: varre por AST os arquivos versionados
  e falha listando cada import proibido. O mapa de proibições:
  - `domain` não importa nada interno;
  - `application` importa só `domain`;
  - o núcleo (`agents`, `pipeline`, `rag`, `safety`) não importa adapters,
    `interfaces`, `app`, `infra` nem `blueprint`;
  - `tools` e `adapters` não importam a borda (`interfaces`) nem a composição
    (`app`).
- `agents/diagnoser.py`: recebe `client_factory` por injeção. O `main`, raiz
  de composição, passa a fábrica do adapter Anthropic.
- `tools/slack_setup.py` → `app/slack_setup.py` (com `git mv`), porque montar
  um notificador da borda é trabalho da raiz de composição. Referências
  atualizadas em `main.py`, no `conftest` e em `test_cli_isolation.py`.

**Teste:** antes da correção, o teste listou exatamente as 2 violações.

**Suíte completa:** ruff limpo. unit 1472 passaram, integration 138
passaram, suíte completa 1650 passaram e 10 foram pulados. Status 0 nos três.

**Fora:** o import-linter como step de CI exige mexer em `.github/`. O teste
de AST cumpre o mesmo papel dentro dos jobs que já existem.

## P3-3b — Portas da memória e das fontes de telemetria (A4/ARQ-16)

**Situação.**

- `IncidentMemoryDB` (SQLite) não tinha porta. O orchestrator, o
  `SkillRouter` e o `SkillEvolver` dependiam da classe concreta.
- As portas das fontes externas (`MetricsSource`, `LogSource`, `TraceSource`,
  `IncidentTracker`) viviam em `tools/interfaces.py`, em paralelo a
  `application/ports`.

**Mudança** (só estrutura, sem alterar comportamento):

- `application/ports/incident_memory.py`: `IncidentMemory`, um `Protocol`
  `runtime_checkable` com as operações que agentes e composição usam. Para
  manter `application/` dependendo só de `domain/`, os tipos de valor ficam
  como `Any`.
- `application/ports/telemetry_sources.py` e `incident_tracker.py`: as classes
  abstratas foram movidas para cá.
  - `tools/interfaces.py` reexporta as quatro (é a mesma classe), então os
    imports existentes continuam valendo.
  - As implementações simuladas, a do Databricks e a do Jira continuam em
    `tools/`.
- Os três agentes anotam `IncidentMemory`, e não `IncidentMemoryDB`.

**Testes:** `tests/test_memory_and_source_ports.py`, 5 testes.

- **Antes da mudança:** os 5 falharam, porque as portas não existiam.
- **Cenários cobertos:**
  - a implementação SQLite satisfaz a porta;
  - as portas reexportadas são as mesmas classes;
  - os agentes não importam `IncidentMemoryDB`.

**Suíte completa:** ruff limpo. unit 1477 passaram, integration 138
passaram, suíte completa 1655 passaram e 10 foram pulados. Status 0 nos três.

## P3-5 — Documentação que descreve o projeto atual (M1/M2, F5-05/07/13/14, ARQ-17)

**Problema:** guias com comandos que não sobem nada, cenário inexistente,
prefixo de variável errado, links para arquivos inexistentes, números de março
e um plano de refatoração sem status.

**Portão:** `tests/test_docs_consistency.py` (unit, 4 testes), sobre todo `.md`
versionado, exceto os relatórios datados (`analise-*`, `p*-progresso`):

- todo link relativo resolve para um arquivo;
- todo `uvicorn pacote.modulo:objeto` aponta para um objeto definido no módulo
  (por AST);
- todo cenário citado em linha com `--scenario` está registrado (o teste importa
  os módulos de cenário, como `main.py`);
- nenhum `AIOPS__` (o loader usa `AIOPS_` + `__` entre seções).

**Antes da correção:** os 4 falharam.
- 4 links quebrados (`_bmad-output/project-context.md` 3x e
  `ARCHITECTURE - AIOPS-AGENT.md`);
- 3x `uvicorn interfaces.http.webhook:app`, que não existe;
- `connection-pool-exhaustion` no CONTRIBUTING;
- `AIOPS__` nos dois READMEs.

Na primeira versão, o teste de cenários passava sem verificar nada: o registro
estava vazio porque nenhum módulo de cenário tinha sido importado. Corrigido.

**O que o teste não cobre:** portas, números e afirmações em prosa. Essas
correções foram feitas à mão e conferidas contra o código:

- `docs/LOCAL_TESTING_SETUP.md`:
  - servidor real `python infra/run_webhook_server.py` em 47890 (igual ao
    contact point do Grafana);
  - mock de Slack em 47892 (padrão do script e do Compose), no lugar de
    8000/8001/8003;
  - exporter em 9101 e job `payment-svc`, no lugar de `metrics-simulator`
    em 9091;
  - removido o caminho pessoal `/Users/bruno/...`.
- `CONTRIBUTING.md`:
  - `.env.local`;
  - cenário `payment-pool-exhaustion`;
  - `--config` é YAML;
  - a regra "@dataclass para todos os modelos" virou "`domain/` é dataclass
    pura; Pydantic só na borda", conforme o ADR 0003.
- `README.md` e `README.pt-BR.md`:
  - `AIOPS_<SECAO>__<CAMPO>`;
  - `config.yaml` está marcado como **não lido** em tempo de execução. Nenhum
    `.py` o carrega. Remover ou ligar o arquivo fica para a P3-7.
- `docs/source-tree-analysis.md`:
  - aviso de que é um retrato;
  - main.py tem ~2400 linhas;
  - removidos `CLAUDE.md` e `ARCHITECTURE…`, que não existem;
  - aponta para a regra de camadas em teste;
  - entrypoints de webhook, MCP e demo.
- `docs/index.md`: monólito com vários entrypoints.
- `docs/project-overview.md`: números medidos com `create_incident()`:
  - 503 logs de 4 serviços + deployment-controller (antes: "6 serviços");
  - 240 amostras por série, 2160 pontos;
  - 100 traces, 278 spans.
- `docs/refactoring-execution-plan.md` (ARQ-17):
  - baseline `7b1a87f`;
  - tabela de status por fase, com o PR e o commit de merge de cada uma;
  - fases 4 e 5 marcadas como parciais, com o que falta;
  - registrado que o shim `data/models.py` não tem critério de remoção.
  - O status vem do histórico de merges, e os comandos de aceite de cada fase
    não foram rodados de novo.
- `SECURITY.md`: os hashes vêm de `mcp.oauth.client_secrets_file`, fora do
  repositório, com comportamento fail-closed.

**Suíte completa:** ruff limpo. unit 1481 passaram, integration 138
passaram, suíte completa 1659 passaram e 10 foram pulados. Status 0 nos três.

## P3-6a — Um só carregador de `.env.local` (M3/F5-10)

**Problema:** cinco parsers artesanais:

- `main()`;
- `main_v1()`;
- `interfaces/demo/server.py`;
- `infra/run_webhook_server.py`;
- `infra/test_slack_notification.py`.

A cópia da demo não fazia strip antes de testar `#`. Nenhuma tratava aspas nem
`export`, então `KEY="v"` chegava com as aspas ao cliente.

**Mudança:**

- `config/env.py`: `load_local_env(root=None)`, sobre o `python-dotenv`, que já
  era dependência. Carrega `<raiz>/.env.local` e depois `<raiz>/.env`, com
  `override=False`.
  - Precedência: processo > `.env.local` > `.env`. Antes, em `main()`, o
    `.env` vencia o `.env.local`; a nova ordem segue o nome "local".
  - A raiz padrão é a do repositório, não o diretório corrente. A CLI, que
    usava o cwd, passa a ler o mesmo arquivo que a demo e os scripts de infra.
- As 5 cópias foram trocadas por chamadas à função. O `from dotenv import
  load_dotenv` saiu de `main.py`.
- `tests/conftest.py`: o fixture autouse `isolated_cli` aponta
  `config.env.REPO_ROOT` para um diretório vazio. Antes, com o pytest rodando
  na raiz, os testes de `main()` liam o `.env.local` real (só o Slack era
  desligado). Agora nenhum teste lê arquivo de credencial do checkout.
- 4 testes faziam patch de `main.load_dotenv` e passaram a fazer de
  `main.load_local_env`. `test_cli_isolation` passa `tmp_path` ao fixture.

**Testes:** `tests/test_local_env_loader.py`, 5 testes.

- **Antes da mudança:** os 4 testes iniciais falharam (módulo inexistente; o
  portão listou 4 arquivos com parser manual).
- **Cenários cobertos:**
  - aspas, `export` e comentário indentado;
  - não sobrescreve o processo;
  - `.env.local` vence `.env`;
  - arquivo ausente;
  - a suíte não lê o `.env.local` do checkout;
  - portão contra parser manual novo fora de `config/env.py`.

**Smoke:** `import interfaces.demo.server` e execução do script do webhook
(sem subir servidor) no venv completo.

**Suíte completa:** ruff limpo. unit 1486 passaram, integration 138
passaram, suíte completa 1664 passaram e 10 foram pulados. Status 0 nos três.

## P3-6b — Redação de segredos em todos os entrypoints (M4/F5-06)

**Problema:**

- `configure_logging()`, que instala o processador `_redact_secrets`, só era
  chamado em `main_v1`. Em `main()` (V0), MCP, demo e webhook, o structlog
  usava o padrão: saída em stdout, sem redação.
- `main.py` tinha a própria lista de padrões, com 4 regex contra 6 em
  `core/logging.py`. `_sanitize_secrets("xoxb-…")` devolvia o token intacto, e
  essa função limpa a mensagem de erro enviada ao Slack em `pipeline.failed`.

**Mudança:**

- `core/logging.py`:
  - `SECRET_PATTERNS` público e `redact_text()`;
  - o processador usa `redact_text`;
  - `_SECRET_PATTERNS` continua como alias.
- `main.py`:
  - `API_KEY_PATTERNS = SECRET_PATTERNS`;
  - `_sanitize_secrets = redact_text`, que agora cobre `xoxb-` e `Bearer`;
  - `main()` chama `configure_logging(AIOPS_LOG_LEVEL ou WARNING)`. O padrão
    da CLI é WARNING, para não intercalar JSON com a saída do Rich.
- `configure_logging()` também é chamado em:
  - `interfaces/demo/server.py:main()`, no lugar de `logging.basicConfig`;
  - `__main__` de `interfaces/mcp/server.py`. Lá o stdout é o canal do
    protocolo stdio, e o log passa a ir para stderr;
  - `__main__` de `infra/run_webhook_server.py`.
- `create_app` (`interfaces/http/app.py`) não tem entrypoint real e ficou de
  fora. É assunto da P3-2.
- `tests/conftest.py`: novo fixture autouse `_restore_logging_config`. Ele
  restaura os handlers do root logger e a configuração do structlog após cada
  teste, porque `main()` agora reconfigura o logging global.

**Mudança visível:** na CLI V0, os eventos INFO do structlog deixam de sair
em stdout (formato console). Agora saem em JSON no stderr, a partir de
WARNING, ou do nível definido em `AIOPS_LOG_LEVEL`.

**Testes:** `tests/test_logging_entrypoints.py`, 7 testes.

- **Antes da mudança:** 5 falharam. Os 2 que passavam são o caso de
  `main_v1`, que já configurava, e o processador, que já redigia quando ativo.
- **Cenários cobertos:**
  - a lista de padrões é a mesma;
  - `_sanitize_secrets` redige `xoxb-` e `Bearer`;
  - o log configurado sai em JSON com `[REDACTED]`;
  - um portão AST confere a chamada em cada um dos 5 entrypoints.

**Suíte completa:** ruff limpo. unit 1493 passaram, integration 138
passaram, suíte completa 1671 passaram e 10 foram pulados. Status 0 nos três.

## Correção das entradas P3-6a e P3-6b (revisão)

Uma revisão apontou duas afirmações acima que não tinham sido medidas.
Medidas agora, as duas estavam erradas:

1. **P3-6b, "redação em todos os entrypoints":** o `ProcessorFormatter` não
   tinha `foreign_pre_chain`, então o que vinha de `logging.getLogger` saía sem
   redação. A demo usa esse caminho, e o relatório conta 13 módulos assim.
   - Medido: `logging.getLogger("x").warning("token xoxb-…")` depois de
     `configure_logging()` saía com o token.
   - Correção: `foreign_pre_chain=shared_processors`.
   - Teste: `test_configured_logging_redacts_stdlib_loggers` falhou antes e
     passou depois.
2. **P3-6a, "nenhum teste lê o `.env.local` do checkout":** valia só dentro do
   fixture. `tests/test_demo_narrative_endpoint.py:13` importa
   `interfaces.demo.server` no nível de módulo, e esse import carrega o env
   durante a coleta, com a raiz real. Isso já acontecia antes, com o parser
   antigo da demo.
   - Correção: `tests/conftest.py` redireciona `config.env.REPO_ROOT` no próprio
     import, para um caminho inexistente. Os scripts de infra chamam
     `load_local_env()` sem argumento e por isso também obedecem.
   - Teste: `test_collection_never_reads_the_checkout_env_files` captura
     `REPO_ROOT` no import do módulo de teste, durante a coleta.
   - Mutação: com a linha do conftest desligada, o teste falha; com a linha
     restaurada, passa.

Ajustes de texto:
- README (EN/PT) e CONTRIBUTING agora dizem que todos os entrypoints carregam
  `.env.local` e depois `.env`, e que `.env.local` prevalece.
- No plano de refatoração, a coluna de status diz "merged" no lugar de
  "done", porque os comandos de aceite não foram rodados de novo.

**Suíte completa:** ruff limpo. unit 1495 passaram, integration 138
passaram, suíte completa 1673 passaram e 10 foram pulados. Status 0 nos três.

## P3-6c — Cenário com fonte única (F5-11): fora

Ficou de fora. É uma escolha para o Allan, não algo que eu deva decidir.

**Os dois geradores diferem na forma, não só nas constantes:**

- **CLI** (`data/sample_incident.py`): 1 hora de dados. A falha é abrupta,
  depois de um deploy que reduz o pool de 200 para 50. Serviços
  `payment-svc`, `gateway`, `auth-svc` e `order-svc`.
- **Demo** (`interfaces/demo/scenarios.py`): replay de 32 minutos em curva
  logística, com o pool em 200 e sem redução, calibrado para scores de risco
  por minuto (narrativas e limiares dependem disso). Serviços
  `payment-service`, `db-pool`, `api-gateway` e `cache`.

**Opções:**

- **Unificar a forma:** muda os goldens e snapshots da CLI ou a linha do tempo
  calibrada da demo.
- **Renomear os ids da demo:** muda os parâmetros de URL, os valores do
  `<select>` e as chaves de narrativa, e continua com dois geradores.

## P3-7 — Empacotamento e arquivos estranhos (M5/M6, F5-15)

**Problema:**

- `pyproject.toml` só tinha `[build-system]` (maturin) e `[tool.*]`, sem
  `[project]` nem `requires-python`.
- Em `requirements.txt`, PyJWT, cryptography e httpx ficavam sob "# Testing",
  mas `auth/` e `agents/a2a_client.py` os importam.
- Não havia lockfile.
- Havia arquivos estranhos versionados.

**Mudança:**

- `pyproject.toml`:
  - `[project]` com `requires-python = ">=3.12,<3.13"` e as dependências de
    runtime, agora com httpx, PyJWT e cryptography;
  - `[dependency-groups]` `test` e `build`;
  - `.python-version` = 3.12.
- **Nome da distribuição: `aiops-rust`.** O backend é o maturin, então o
  `[project]` vira o metadado da wheel do job Rust do CI. Com o nome
  `aiops-agent`, medi que a wheel passava a se chamar
  `aiops_agent-0.1.0-…whl`. Como o CI guarda `rust/target` em cache e instala
  `rust/target/wheels/*.whl`, a wheel antiga do cache entraria junto no glob.
  Com `aiops-rust`, o arquivo tem o mesmo nome de antes e é sobrescrito.
- **Medido:** o `maturin build` com o pyproject novo (no scratchpad) gera
  `aiops_rust-0.1.0-cp312…whl` com o mesmo conteúdo (`_aiops_rust`). A
  simulação `uv pip install --dry-run --offline` no venv completo resolve 96
  pacotes já instalados e só troca a própria wheel.
- `requirements.txt` continua sendo a entrada do CI, porque mudar `.github/`
  não é comigo. O conjunto de requisitos é idêntico ao anterior (conferido com
  `diff` dos conjuntos ordenados). Mudaram só a ordem e os comentários:
  runtime, grupo build, grupo test.
- `uv.lock`: 121 pacotes, Python 3.12.*, sem caminho pessoal.
  `uv lock --check --offline` sai com status 0.
- Arquivos removidos (`git rm`, só dentro do repositório):
  - `docs/project-scan-report.json`, com o caminho pessoal `/Users/bruno/…`;
  - `docs/research-python-protocol-best-practices.py`;
  - `config.yaml`, que nenhum `.py` lia. Junto saiu o teste que só verificava
    que ele existia
    (`test_reranker_integration.py::test_project_config_yaml_has_expected_services`).
    Os testes que passam o mapa ao `DomainReranker` a partir de YAML continuam;
  - `env.example`, que era só um ponteiro de 2 linhas para `.env.example`.
    O README em português dizia que ele trazia as variáveis de `infra/`, o
    que não era verdade.
- READMEs, `source-tree-analysis` e CONTRIBUTING foram atualizados. O
  CONTRIBUTING ganhou o caminho `uv sync --group test --group build`.

**O que não foi feito:**

- **Lista própria do job unit em `.github/workflows/ci.yml`:** os pisos
  divergem (por exemplo, anthropic>=0.40.0) e a lista é repetida em
  `scripts/ci-local.sh`. Fazer o CI instalar a partir do pyproject exige
  mexer em `.github/`, que é decisão do Allan. O `ci-local.sh` espelha esse
  job e ficou como está, para não divergir dele.
- **`.env.example`:** a leitura foi negada pelas permissões. Não conferi o
  conteúdo dele.

**Testes:** `tests/test_packaging_consistency.py`, 5 testes.

- **Antes da mudança:** os 5 falharam.
- **Cenários cobertos:**
  - `requires-python` e `.python-version`;
  - `requirements.txt` é exatamente `dependencies` mais os grupos;
  - jwt, cryptography e httpx são importados fora de `tests/` e estão em
    `dependencies`.

**Suíte completa:** ruff limpo. unit 1499 passaram, integration 138
passaram, suíte completa 1677 passaram e 10 foram pulados. Status 0 nos três.

## P3-8a — Snapshot ausente falha; paridade Rust ↔ Python de verdade (T-g, T-f)

**Snapshot (T-g):** quando o arquivo de snapshot não existia,
`SnapshotManager.assert_match` e `assert_text_match` o criavam e passavam.
Um snapshot apagado ou esquecido no commit passava no CI, comparando a saída
com ela mesma.

- **Agora:** sem `--snapshot-update`, snapshot ausente falha, com a instrução
  de como gerá-lo, e nada é criado. Com a flag, o arquivo é criado ou
  sobrescrito.
- Nenhum snapshot versionado estava faltando: a suíte segue verde e o
  `git status` ficou limpo.
- Nenhum outro golden da suíte se autogera (busca por `if not …exists()` em
  `tests/`).
- **Teste:** `tests/test_snapshot_manager_missing.py`, 4 testes. Antes da
  mudança, 2 falharam: os casos de arquivo ausente, nos formatos JSON e texto.

**Paridade (T-f):** `TestDetectLatencyDriftEquivalence` chamava
`detect_latency_drift`, que delega ao Rust quando a extensão existe. Comparava
Rust com Rust.

- `tests/rust/test_rust_python_parity.py` (marcador `rust`, 54 testes) chama a
  mesma API pública duas vezes: uma pelo caminho Rust e outra com
  `_RUST_AVAILABLE = False`. Cobre:
  - `detect_latency_drift`: casos-limite e entradas aleatórias, com 3
    limiares;
  - `MetricFilter` e `TraceFilter`: os 3 cenários;
  - a classificação de taint do sanitizer, unitária e em lote, e
    `sanitize_logs`: os 3 cenários e 14 textos hostis.
  - `custom_patterns` ficou de fora, porque o Rust não os recebe (B15, P3-4).
- **Divergência real encontrada:** o `TraceFilter` pelo caminho Rust devolvia
  os atributos do span gargalo como `str` (`'23'`, e `true` em vez de `True`).
  O fallback Python mantém os tipos. Falhavam os cenários memory-leak e
  unknown-fault. `agents/diagnoser.py:755` usa `str(attributes)`.
  - Correção em `pipeline/trace_filter.py::_filter_rust`: devolve os atributos
    originais do span que o próprio Rust apontou como gargalo (mesmo
    trace_id, serviço e duração).
- A classe antiga de equivalência ganhou um fixture que força o fallback
  Python. Assim, `py_result` passa a vir de fato do Python.

**Benchmarks (T-h):** ficaram de fora. Os benchmarks já têm
`@pytest.mark.slow`, mas o job `rust-build` roda `pytest tests/` sem excluir
`slow`. Tirá-los do caminho do CI exige mudar `.github/` ou o `addopts`
padrão que esse job usa, ou seja, mudar um portão de CI. Isso é decisão do
Allan.

**Suíte completa:** ruff limpo. unit 1503 passaram, integration 138
passaram, suíte completa 1735 passaram e 10 foram pulados. A suíte completa
inclui `tests/rust` com a extensão compilada. Status 0 nos três.

## P3-8b — Testes nomeados por comportamento (M7)

**Renomes** (`git mv`, histórico preservado):

- `tests/epic2/` virou `tests/webhook_ingest/`, com os arquivos
  `test_health_and_webhook`, `test_normalizer_registry`, `test_normalizers`,
  `test_dedup` e `test_pipeline_runner`;
- `test_epic7_incident_memory` virou `test_incident_memory`;
- `test_epic9_cot_retriever` virou `test_cot_retriever`;
- `test_story_1_6_pipeline_nodes` virou `test_blueprint_pipeline_nodes`;
- `test_story_1_7_safety_nodes` virou `test_blueprint_safety_nodes`;
- `test_story_1_8_wire_main` virou `test_main_blueprint_wiring`.

As docstrings de módulo e os comentários de seção perderam o prefixo
"Story N.N" / "Epic N". O plano de refatoração cita o diretório novo.

**Os dois skips "Aguarda Story 3.x"** de `tests/test_notifications.py` agora
descrevem o que falta: o `BlueprintEngine` não tem `add_observer`/`add_node`
nem `run()` assíncrono, e os eventos de ciclo de vida são emitidos fora do
engine. Apagar ou implementar é decisão de desenho, por isso continuam
pulados. Eles entram na lista do que ficou de fora.

**Suíte completa:** ruff limpo. As contagens são idênticas às da P3-8a, ou
seja, nenhum teste se perdeu no renome. unit 1503 passaram, integration 138
passaram, suíte completa 1735 passaram e 10 foram pulados. Status 0 nos três.

## P3-4 — Fronteira Python↔Rust (A8, B15: F5-08, ARQ-12, ARQ-13, F12, F15)

**Limiares numa fonte só (F5-08/ARQ-13):**

- `filter_metrics(metrics, sigma_threshold, baseline_ratio)` e
  `filter_traces(spans, percentile_threshold, max_hops)` recebem os limiares
  como argumentos obrigatórios. Os literais 3.0/0.5 e 95.0/3 do Rust saíram.
- `MetricFilter`/`TraceFilter` delegam ao Rust sempre que ele existe,
  passando os limiares da instância. Saiu a guarda por igualdade de float,
  que desligava o Rust sem aviso quando o YAML mudava.
- Os defaults de `MetricFilter`, `TraceFilter` e `Aggregator` vêm de
  `ThresholdsConfig`. O YAML sobrescreve na composição.

**EWMA/CUSUM único:** `rust/src/lib.rs` perdeu as cópias inline de
`detect_cusum`/`compute_ewma_slope` e registra as de `cusum_ewma/`, que eram
as testadas pelo `cargo test` mas não eram exportadas. A cópia Python
(`_python_ewma_slope`) continua como fallback e ganhou teste de paridade.

**`custom_patterns` (ARQ-12):** com padrões customizados, o sanitizador usa o
caminho Python, que os conhece. É a opção que o relatório aceita. Antes,
eles eram ignorados quando o Rust estava instalado. A decisão é dinâmica
(`_use_rust()`), para o teste de paridade poder alternar o caminho.

**Encontrado pela paridade com limiares fora do padrão:** a saída Rust de
`MetricFilter` e `TraceFilter` não era determinística quando havia empate.
A iteração de `HashMap` é aleatória por processo.

- Medido: com sigma=1.5 no unknown-fault, a ordem diferia do Python em 5 de 6
  processos.
- No `TraceFilter`, a iteração também decidia qual trace sobrevivia na
  deduplicação.
- Correção no Rust: os dois filtros agrupam e deduplicam na ordem de primeira
  aparição, como o dict do Python.
- Testes de empate: 10 séries ou traces empatados. Uma ordem aleatória
  coincidir com a do Python tem chance de 1 em 10!.

**F12 (contagem do fallback do LogParser):**

- Antes, acima de 500 logs, só a amostra era contada.
- Primeiro tentei contar com peso (total/amostra). O teste novo mostrou que
  isso não serve: com erros periódicos, a amostragem sistemática faz aliasing
  e 60 erros viravam 120.
- Ficou a outra correção do relatório: minerar (`add_log_message`) só a
  amostra e atribuir todos os logs com `miner.match(…, "fallback")`. O log
  sem cluster compatível é minerado.
- Até 500 logs nada muda: os cenários têm cerca de 503 logs, e 503 // 500 = 1.

**F15 (timestamps):**

- O bridge Rust do log parser passa a devolver epoch, como o de métricas.
- Os wrappers convertem no fuso da entrada: aware continua aware, naive
  continua em hora local.
- No `TraceFilter` via Rust, o timestamp é o `start_time` do primeiro span do
  trace, como no fallback.

**Rust:**

- `cargo test --locked` passou (15 testes), com `CARGO_TARGET_DIR` no
  scratchpad.
- A wheel release foi construída no scratchpad e reinstalada no venv
  completo.
- `tests/rust` rodou 3 vezes em processos separados, com 147 testes passando
  em cada rodada.

**Testes:**

- `tests/test_rust_boundary_thresholds.py` (unit, 6 testes, com um `_rust`
  falso). Antes da mudança, 4 falharam. Cenários:
  - os limiares atravessam a fronteira;
  - `custom_patterns` não se perde;
  - o caminho Rust é usado quando não há padrões customizados;
  - os defaults vêm de `ThresholdsConfig`;
  - `lib.rs` sem funções inline e bridges sem literais.
- `tests/test_log_parser_fallback_counts.py` (unit, 4 testes). Antes da
  mudança, os 2 casos amostrados falharam (contavam 500 e 2000 em vez de
  1000 e 2500). Agora a contagem é exata: soma igual ao total e 60 erros.
- `tests/rust/test_rust_python_parity.py` ganhou casos para:
  - limiares fora do padrão (métricas e traces, 3 combinações × 3 cenários);
  - o limiar chegando de fato ao Rust;
  - `custom_patterns` com Rust instalado;
  - `compute_ewma_slope`;
  - empates;
  - timestamps aware com o fuso local forçado para America/Sao_Paulo.
- Contra a extensão antiga, falharam os testes de empate e o de timestamps
  do log parser.

**Suíte completa:** ruff limpo. unit 1513 passaram, integration 138
passaram, suíte completa 1798 passaram e 10 foram pulados. A suíte completa
roda com a extensão nova. Status 0 nos três.

## P3-2 — Entrypoint real para `create_app`; DI global morto removido (A5, ARQ-09, F5-04)

**Feito:**

- `interfaces/http/__main__.py`: `python -m interfaces.http [--host] [--port]
  [--config]` serve o `create_app` em `127.0.0.1:47893`, ao lado do webhook
  (47890), da demo (47891) e do mock de Slack (47892).
  - Antes de montar o app, carrega `.env.local`/`.env` e chama
    `configure_logging()`.
  - Os três servidores (HTTP, demo e webhook) passam `log_config=None` ao
    uvicorn, então os logs dele também saem em JSON com redação.
  - **Access log:** a demo e o webhook passam `log_level="info"` e mostram as
    linhas de acesso. O HTTP não mostra, de propósito: `configure_logging()`
    deixa `uvicorn.access` em WARNING, e o `session_id` do MCP SSE vai na query
    string. (Achado do pré-PR: a demo tinha perdido o access log.)
- **Medido com o processo real** (script no scratchpad): `/health` respondeu
  200. As 10 linhas de log saíram em JSON. Antes do `log_config=None`, só 2
  de 11 saíam em JSON.
- **O que sobe:**
  - health;
  - A2A, que exige JWT e rejeita tudo sem segredo;
  - o servidor OAuth, só com chave configurada;
  - o MCP via SSE, só com opt-in `mcp.enabled: true`. Ligado, o
    `auth_method` padrão é `header`, que é uma das decisões do Allan. O
    README diz isso.
- **DI concorrente que nunca funcionava, removido:**
  - `AppState`, `app_state` e o `knowledge_store` global de
    `interfaces/http/app.py`;
  - `interfaces/http/dependencies.py`, cujos asserts sempre falhariam, porque
    nada populava o `app_state`;
  - o teste que só verificava que essas funções existiam.
  - O padrão que fica é o `request.app.state`, já usado por A2A, webhook e
    accuracy.
- **Testes:** `tests/test_http_entrypoint.py`, 4 testes. Antes da mudança,
  os 4 falharam. Cenários:
  - porta e host padrão, `log_config` e `/health` via TestClient;
  - `--host`, `--port` e `--config` chegam ao app;
  - logging e env;
  - o DI global sumiu.
  - O portão de logging da P3-6b agora inclui este entrypoint.

**Não feito (decisão do Allan):** montar ou apagar os routers accuracy,
metrics, knowledge e admin.

- Nenhum deles tem autenticação.
- Três escrevem: o PATCH de acurácia, o POST de padrões de conhecimento
  (influencia o diagnóstico) e o POST admin de validação.
- O `PipelineMetricsCollector` não é alimentado por ninguém, e o
  `IncidentStore` do processo HTTP só conteria os incidentes desse processo.
- Montar exige escolher auth/escopo e as fontes de dados. Apagar remove
  funcionalidade testada.
- Também não removi os helpers sem chamador fora dos testes (`LazyRAG`,
  `create_engine`/`NotificationEngine`, `handle_prediction_event`), pelo
  mesmo motivo.

**Suíte completa:** ruff limpo. unit 1517 passaram, integration 138
passaram, suíte completa 1802 passaram e 10 foram pulados. Status 0 nos três.

---

## O que ficou de fora da P3, e por quê

| Item | Motivo |
|---|---|
| **P3-1**: decompor `main.py` | Depende da escolha V0×V1, que é do Allan. O relatório pede para fazer depois do P2-1, para não decompor o pipeline que vai morrer. |
| **P3-3**: import-linter no CI | Exige mudar `.github/`. No lugar, a regra de camadas virou teste AST nos jobs existentes (`tests/test_layer_dependencies.py`). |
| **P3-6c**: cenário com fonte única (F5-11) | Os geradores da CLI e da demo diferem na forma: 1 h com falha abrupta e pool de 200 para 50, contra uma rampa logística de 32 min calibrada para a narrativa. As duas saídas (unificar a forma ou renomear os ids da demo) são escolha de produto. |
| **P3-7**: o CI instalar a partir do `pyproject` | Exige mudar `.github/` (o job unit tem lista própria, com pisos divergentes). O `requirements.txt` ficou como espelho, travado por teste. Não conferi o `.env.example`, cuja leitura foi negada pelas permissões. |
| **P3-8**: benchmarks fora do caminho padrão (T-h) | Já têm `@pytest.mark.slow`. Excluí-los do job `rust-build` muda um portão de CI (`.github/` ou o `addopts` que o job usa). |
| **P3-8**: os 2 skips do observer do `BlueprintEngine` | Testam uma API que não existe (`add_observer`, `run` assíncrono). Implementar ou apagar é decisão de desenho. O motivo do skip agora diz isso. |
| **P3-9c**: rota/CLI para aprovar propostas de skill | A gravação pendente foi feita; o caminho de aprovação para uma pessoa não foi. |
| **P3-9d**: ciclo de skills no V1 | Depende da escolha V0×V1. |
| **P3-2**: routers accuracy, metrics, knowledge e admin e os helpers sem chamador | Montar exige decidir autenticação e fonte de dados; apagar remove funcionalidade testada. |
| **MCP SSE com `auth_method: header`** | Remover `header` como padrão é decisão listada do Allan. O entrypoint novo não liga o MCP: ele continua opt-in. |
| **P3-4**: `custom_patterns` repassado ao Rust | Foi adotada a alternativa que o relatório aceita: com padrões customizados, o sanitizador usa o caminho Python. O Rust continua sem recebê-los. |
| **P3-5**: `docs/source-tree-analysis.md` | Recebeu aviso de retrato e correção das afirmações falsas, mas não foi reescrito. Contagens por arquivo, complexidade e nomes de modelo podem estar velhos. |
| **P3-8 (M7)**: menções a story em comentários | Os nomes de arquivo, de teste e de classe não citam mais o backlog: 0 nomes, medido com `git grep`. Restam 61 menções "Story N" em comentários e docstrings, em 40 arquivos de `tests/`. |
| **P3-9**: faixas de versão vulneráveis e OIDC refazendo o JWKS com `kid` desconhecido | Já feitos na P2: `117c5d3` (F08) e `382f3b7`. |
| **Dependabot** (`.github/dependabot.yml`, só lido) | Ecossistema pip em `/`, com PRs agrupados mensais. Se ele atualizar só `requirements.txt` ou só `pyproject.toml`, `test_requirements_txt_is_exactly_pyproject` reprova o PR. O `uv.lock` não é atualizado pelo ecossistema pip. Ajustar (por exemplo, o ecossistema `uv`) exige mexer em `.github/`. |

## Revisão final (antes de encerrar)

- **Clone limpo:** os três jobs rodaram num `git clone` da branch no scratchpad
  (não no worktree): unit 1517, integration 138 e completa 1802 passaram, com
  10 pulados. Status 0 nos três. Isso descarta snapshot que existisse só no
  disco local.
- **Job unit do CI:** o `uvicorn`, que o teste do entrypoint importa, não está
  na lista do job, mas chega como dependência do `mcp` (`uvicorn>=0.31.1`).
- **Extensão Rust antiga com o Python novo:** medido. Com a wheel anterior à
  P3-4 no `PYTHONPATH`, `main.py --no-llm --no-rag` saía com 1 e
  `TypeError: filter_metrics() takes 1 positional arguments but 3 were given`.
  - Correção: `pipeline/rust_compat.py::has_params` lê o `__text_signature__`
    da função PyO3. Sem os parâmetros novos, aquela função conta como
    indisponível, o pipeline usa o fallback Python e avisa para recompilar
    com `scripts/build_rust.sh`.
  - Depois da correção: com a wheel antiga, saída 3 (aprovação humana, o
    resultado do cenário), aviso registrado e nenhum traceback. Com a wheel
    nova, saída 3 e nenhum aviso.
  - Teste: `tests/test_rust_compat.py`, 3 testes.
- **`uv sync`:** a simulação (`--dry-run`) mostrou que, sem
  `--no-install-project`, o uv instala `aiops-rust` editável, o que compila
  a extensão. O CONTRIBUTING agora indica
  `uv sync --frozen --no-install-project --group test`.
- **CONTRIBUTING:** a lista "7 módulos stdlib-only" já estava errada antes
  desta P3, porque os filtros importam numpy. Agora a regra diz o que vale:
  nenhum import de ML pesado.
- **SECURITY.md:** registra o novo listener (`python -m interfaces.http`,
  127.0.0.1:47893).

**Suíte completa:** ruff limpo. unit 1520 passaram, integration 138
passaram, suíte completa 1805 passaram e 10 foram pulados. Status 0 nos três.

## Revisão pré-PR (`pre-pr-contradicoes` em `2b6bf7b`)

Quatro achados sobreviveram à refutação. Todos foram corrigidos com teste
que falha no código antigo (medido no clone em `2b6bf7b`: 9 falhas, 20
passaram) e passa no novo.

- **MCP `validate_action` dizia "TNR check passed"** quando o orquestrador
  registra `{"verified": False}` (P2-11 mudou o produtor; o adaptador do MCP
  procurava `.message` no dict). Agora a camada diz `TNR not verified: …` e o
  `rationale` não afirma que as 4 camadas aprovaram. Continua sem bloquear,
  como projetado. Teste: `tests/test_mcp_validate_tnr_not_verified.py`.
- **Grafana: grupo `[resolved, firing]` era descartado** como notificação de
  resolução, porque só `alerts[0]` era lido. O normalizador processa o
  primeiro alerta em disparo; só grupo todo resolvido é resolução. Testes em
  `tests/test_grafana_severity_resolved.py`.
- **A skill `default` enfileirava uma proposta por execução** (e uma chamada
  ao LLM), porque ela é roteada sem olhar o score. `should_evolve` não dispara
  enquanto houver proposta pendente para a skill. Medido antes: 4 pendentes e
  4 chamadas em 6 rodadas; agora 1 e 1. Teste em
  `tests/test_skill_score_learning.py`.
- **A demo tinha perdido o access log** com o `log_config=None`. Ela passa
  `log_level="info"`, como o webhook. O HTTP continua sem access log de
  propósito (o `session_id` do MCP SSE vai na query string), e o registro
  acima diz isso. Testes em `tests/test_http_entrypoint.py`.

**Correções leves que vieram dos achados refutados ou secundários:**

- **Resumo da CLI:** `print_summary` pintava `not_verified` de vermelho,
  enquanto o reporter usa amarelo. Agora as duas superfícies concordam. O
  teste `tests/test_summary_tnr_style.py` falhou no código antigo (1 falha,
  2 aprovações) e passa no novo.
- **Tabela de exit codes (README e README.pt-BR):** o `0` dizia "resolvido e
  verificado pelo TNR". Sem medição pós-ação, o TNR é `not_verified`. A
  tabela diz agora o que o código faz. A imprecisão é anterior a esta branch.
- **`tests/test_search_incidents.py`:** o teste de PII só conferia que o
  sanitizador foi chamado. Agora confere também que o e-mail não chega à
  resposta do MCP.
- **Dedup do webhook sem reserva atômica** (pendência anotada na P2): o
  router consultava a dedup, fazia `await submit(...)` e só depois gravava o
  fingerprint. Quando o `submit` cede o event loop, dois envios idênticos
  passavam ambos. Agora consulta e reserva acontecem juntas, sem `await` no
  meio, e a reserva é desfeita se o pipeline recusar ou levantar exceção (o
  retry de um 503 continua funcionando, 4B-01). Medido no código antigo com
  `tests/test_webhook_dedup_concurrency.py`: os 2 envios simultâneos abriram
  2 pipelines (`assert 2 == 1`); no novo, 1 pipeline e 1 resposta
  `deduplicated`.
- **Token não-ASCII no webhook dava 500** (analise-20261109, 4B): o
  `hmac.compare_digest` com `str` não-ASCII levanta TypeError, e o Starlette
  decodifica headers em latin-1. Medido antes da correção:
  `Authorization: Bearer té` no router levantou
  `TypeError: comparing strings with non-ASCII characters is not supported`.
  As três fontes (Grafana, Databricks, PagerDuty) comparam agora em bytes
  UTF-8 (`normalizers/base.py::secrets_match`), e a resposta é 401. Teste:
  `tests/test_webhook_auth_non_ascii.py` (4 falhas antes, 6 aprovações
  depois). O `auth/server.py` compara hashes hexadecimais e não tem o
  problema.
- **`engine.results` compartilhado entre execuções concorrentes** (pendência
  anotada na P2): a mesma engine roda em várias threads (A2A e MCP via
  `to_thread`), e cada `run()` zerava e enchia `self.results`. Cada execução
  usa agora uma lista própria. O efeito era só no log `pipeline.completed`
  (`nodes_run`, `all_success`), o único leitor fora dos testes. A primeira
  versão do teste passava no código antigo por coincidência (1 nó da outra
  execução substituía 1 nó apagado); com 2 nós, o código antigo dá
  `assert 4 == 3` e o novo passa. Teste: `tests/test_engine_concurrent_runs.py`.
- **`POST /mcp/messages` respondia duas vezes** (pendência anotada na P2): o
  transporte do MCP responde pelo `send` do ASGI, e o endpoint devolvia
  `None`, que o FastAPI transformava numa segunda resposta. Medido antes:
  `AssertionError: Received multiple "http.response.start" messages`. Agora o
  endpoint devolve uma resposta que não envia nada. Teste:
  `tests/test_mcp_messages_single_response.py`, com o transporte real.
  **Não feito:** o `GET /mcp/sse` tem o mesmo padrão (devolve `None` depois
  do `connect_sse`), mas o efeito só apareceria na desconexão de um stream
  real, e não medi. Fica anotado.
- **Access log medido no uvicorn, não só no argumento:** depois do
  `configure_logging()`, um `uvicorn.Config(..., log_config=None)` sem
  `log_level` deixa `uvicorn.access` em WARNING, e com `log_level="info"` o
  deixa em INFO (`tests/test_http_entrypoint.py`).
- **Mudança de comportamento da dedup:** um envio idêntico que chega
  enquanto o primeiro ainda está no `submit` recebe 202 `deduplicated`, mesmo
  que o primeiro termine em 503 (fila cheia). A reserva é liberada na recusa,
  então o próximo reenvio do emissor é aceito.

## Segunda revisão pré-PR (`c89f52e`, os 8 commits depois de `2b6bf7b`)

Base do PR decidida pelo Allan: `main`. A revisão foi estática, sem M1, e
mediu só com checagens leves. Dois achados sobreviveram, dos quais um tinha
duplicatas. Oito foram refutados. Corrigidos:

- **`--help` contradizia os READMEs:** o epílogo dizia que o exit 0 é
  "TNR verified", e o README corrigido diz que, sem medição pós-ação, o TNR
  é `not_verified`. Medido em `c89f52e`: o `--help` imprimia "TNR verified".
  O texto agora diz o mesmo que o README, e `tests/test_exit_codes.py`
  barra a volta. O rótulo `resolved` fica, porque é um identificador estável
  fixado por testes.
- **O "Equivalente" do app HTTP não era equivalente:** a docstring de
  `interfaces/http/__main__.py` indicava `uvicorn ...:create_app --factory`.
  Esse caminho não chama `configure_logging()`: os logs saem sem redação e o
  access log em INFO grava o `session_id` do MCP SSE. A docstring agora manda
  usar `python -m interfaces.http` e diz por quê.

## Terceira revisão pré-PR (`eee1245`, delta `c89f52e..eee1245`)

Revisão estática, sem M1. Um achado sobreviveu e os outros foram refutados.

- **O postmortem afirmava que o TNR validou a mitigação**, logo abaixo de
  `Result: not_verified`. Ele também dizia que o pipeline concluiu todas as
  etapas com sucesso, mesmo com a aprovação humana pendente. É a mesma
  contradição corrigida no `--help` e nos READMEs, e os dois snapshots de
  postmortem fixavam o texto errado. As duas lições agora seguem o
  resultado do TNR (`success`, `regression`, `not_verified`) e o gate
  humano. Os snapshots `postmortem_structure.txt` e
  `unknown_fault_postmortem_structure.txt` foram atualizados com o texto
  novo. Medido: com o `main.py` antigo e os snapshots novos,
  `test_postmortem_structure_snapshot` falha. Com o novo, `test_snapshots.py`
  (12 testes) e `test_unknown_fault.py` (26 testes) passam.

## Quarta revisão pré-PR (`00cb1c5`, delta `eee1245..00cb1c5`)

Revisão estática, sem M1. Dois achados sobreviveram, cada um com
duplicatas. Os outros oito foram refutados.

- **Summary × Lições com aprovação pendente:** o Summary dizia
  `Resolution: Resolved` e as Lições, no mesmo postmortem, "Pipeline stopped
  at the human approval gate". A linha `Resolution` já existia. A contradição
  dentro do documento nasceu da lição nova. Agora o veredito do gate é
  calculado uma vez e vale para os dois. Com aprovação pendente, o Summary
  diz `Awaiting human approval (mitigation proposed, not executed)`. Os dois
  snapshots foram atualizados.
- **"completed all stages" numa saída antecipada:** sem incidente
  (`DETECTING -> IDLE`) ou com diagnóstico `FAILED`, não há mitigação, e a
  lição dizia que o pipeline concluiu todas as etapas. A lição de detecção
  dizia "identified the incident promptly". Agora as duas descrevem a parada.
- **Medido:** `tests/test_postmortem_coherence.py`, 3 testes. Com o
  `main.py` antigo, 2 falham, e o terceiro guarda o caso auto-aprovado, que
  já funcionava. Com o novo, os 3 passam, e também `test_snapshots.py` (12) e
  `test_unknown_fault.py` (26).

## Revisão do `step_postmortem` inteiro (`9db0afd`)

Pedido do coordenador: revisar a função inteira de uma vez, em vez de um
achado por rodada. A revisão foi estática, sem M1, e cobriu aprovação
automática, aprovação pendente, sem incidente, diagnóstico FAILED,
mitigação falha ou bloqueada e os caminhos V1 e Databricks. Catorze achados
sobreviveram, agrupados em sete defeitos. Dois foram refutados. Todos foram
corrigidos num único commit.

1. **"Mitigation applied"** saía na Timeline e no título em toda combinação,
   mas nada no projeto executa a ação. A linha da Timeline saiu. A seção
   passou a se chamar "Mitigation Proposed" e diz que o agente não executa.
   Sem mitigação, ela diz que nenhuma foi proposta. A Duration diz que é
   do cenário (do deploy ao horário de referência da correção).
2. **A transição VERIFYING -> RESOLVED** dizia "human approval still
   pending", mas era gravada em `step_agents`, antes de o gate decidir.
   Agora diz que o gate decide em seguida.
3. **Aprovação automática com TNR `not_verified`:** "Resolved" vinha sem
   qualificação. Agora a Resolution diz `Resolved (mitigation approved, not
   executed by the agent; TNR not_verified)`, e a lição diz que a execução
   fica fora do agente. "Resolved" puro só sai com TNR `success`.
4. **O JSON não carregava o veredito:** `meta.success` era true com a
   aprovação pendente. O `meta` ganhou `resolution`, `awaiting_human`,
   `mitigation_proposed` e `safety_blocked_by`, calculados pela mesma função
   do markdown (`_postmortem_verdict`). As 10 chaves de topo, que são API
   estável, não mudaram, e a `CONTRIBUTING.md` documenta os campos novos.
5. **Diagnóstico FAILED derrubava o postmortem:** com `root_cause` None, o
   `[:80]` quebrava. Agora o postmortem sai com "Unable to determine root
   cause" e a lição "Diagnosis failed".
6. **"Heuristic diagnosis"** aparecia mesmo com LLM. A lição agora nomeia o
   modelo, diz "Heuristic" só no modo heurístico e "Diagnosis" quando o modelo
   é desconhecido.
7. **"Impact: Service degradation"** saía sem incidente. Agora diz que
   nenhum incidente foi detectado.

**Medido:**

- `tests/test_postmortem_coherence.py` tem 11 testes. Com o `main.py` de
  `9db0afd`, 10 falham e 1 passa (o caso pendente, já corrigido ali). Com o
  novo, os 11 passam.
- Os snapshots foram regenerados com `--snapshot-update`. O diff traz só a
  Duration, a linha removida da Timeline, o título da seção, a linha
  "Executed by the agent" e a lição de diagnóstico.
- `test_snapshots.py` e `test_unknown_fault.py` (38), `test_json_output.py`,
  `test_tnr_not_verified.py` e `test_cli_safety_decision.py` (41) e
  `test_docs_consistency.py` (4) passam. Ruff limpo.
- A suíte completa roda no M1.

## Pré-PR em `78c4689` (delta `9db0afd..78c4689`)

Revisão estática, sem M1. Três defeitos sobreviveram, alguns com
duplicatas. Dez achados foram refutados. Corrigidos num único commit:

- **Sem incidente:** a Resolution e o `meta.resolution` diziam "Requires
  manual intervention", mas a CLI sai com exit 0 ("resolved") numa janela
  saudável. Agora dizem `No incident detected; no action required`. O
  diagnóstico FAILED continua com "Requires manual intervention".
- **`step_tracking` com `root_cause` None:** com `--tracker jira`, o
  `len(None)` derrubava o run antes do postmortem. Root cause, severidade e
  evidências agora têm fallback.
- **A réplica do pipeline em `tests/conftest.py`**, que gera os snapshots,
  ainda gravava a transição antiga ("aguardando aprovação"). Agora usa o
  mesmo texto de `main.step_agents`, e os três snapshots que vêm dela foram
  regenerados. Um teste barra a volta do texto antigo.
- **Medido:** `tests/test_postmortem_coherence.py` passou a ter 13 testes.
  Com o `main.py` de `78c4689`, os 2 que cobrem os defeitos de `main.py`
  falham; com o novo, os 13 passam. `test_snapshots.py` e
  `test_unknown_fault.py` (38) passam. Ruff limpo.

## Pré-PR em `2b4f094` (delta `78c4689..2b4f094`)

Revisão estática no M5, enquanto o CI de `2b4f094` rodava no M1 (verde:
unit 1559, integration 138, completa 1844). Dez achados sobreviveram,
agrupados em quatro defeitos, e quatro foram refutados. A causa comum é que
só `_postmortem_verdict` sabia que não houve incidente. Agora o predicado
`_no_incident(result)` é usado em todos os pontos. Corrigido num único
commit:

- **`--tracker jira` numa janela saudável** abria um Bug "AIOps detected
  incident", e o postmortem, que diz "no action required", apontava para
  ele. Agora `step_tracking` não cria issue sem incidente.
- **Action Items sem incidente:** quatro tarefas `[ ]` apareciam abaixo de
  "no action required", no markdown e no `action_items` do JSON. Agora a
  lista vem vazia, com "None (no incident detected)" no markdown.
- **Resumo da CLI numa janela saudável:** mostrava UNRESOLVED em vermelho
  com exit 0 "resolved". Agora mostra NO INCIDENT.
- **`root_cause` None** ainda quebrava o anexo do postmortem no Jira
  (`_attach_postmortem_to_tracker`) e o `print_summary`, que levava o run a
  exit 1. As demais leituras de `root_cause` (Slack e V1) também passaram a
  usar `or`.
- **Medido:** `tests/test_postmortem_coherence.py` tem 18 testes. Com o
  `main.py` de `2b4f094`, os 5 novos falham; com o novo, os 18 passam.
  `test_tracker_integration.py`, `test_json_output.py`, `test_snapshots.py`
  e `test_unknown_fault.py` (88 testes) passam, e os snapshots não mudaram.
  Ruff limpo.

## Pré-PR em `5e418c3` (delta `2b4f094..5e418c3`)

Revisão estática no M5, enquanto o CI de `5e418c3` rodava no M1 (verde:
unit 1564, integration 138, completa 1849). Dois defeitos sobreviveram, em
cinco achados com duplicatas. Sete foram refutados. Corrigido num único
commit:

- **"tracker error" numa janela saudável:** o `step_tracking` pula de
  propósito sem incidente e devolve o mesmo `None` da falha. O resumo da CLI
  mostrava "tracker error" ao lado de "NO INCIDENT". Agora mostra "not
  created — no incident detected". A falha real do tracker continua sendo
  "tracker error", e a docstring do `step_tracking` diz os dois sentidos do
  `None`.
- **Slack numa janela saudável:** a CLI emitia `diagnosis.completed`, que
  vira "Diagnóstico + Correção Proposta", e um `pipeline.completed` que vira
  "Diagnosis Complete", com Root Cause em branco. Agora `diagnosis.completed`
  não sai sem incidente. O `pipeline.completed` leva `no_incident: true`, e
  o notifier posta "No Incident Detected". O Root Cause vazio volta a
  aparecer como "N/A".
- **Medido:** `tests/test_postmortem_coherence.py` tem 22 testes. Com
  `main.py` e `slack.py` de `5e418c3`, os 3 dos defeitos falham. O da falha
  real do tracker passa nos dois, porque guarda um comportamento que já
  existia. Com o código novo, os 22 passam. `test_notifications.py`,
  `test_tracker_integration.py`, `test_json_output.py` e `test_snapshots.py`
  passam (98 testes, 5 pulados). Ruff limpo.
