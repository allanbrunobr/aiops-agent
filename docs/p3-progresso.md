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
