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
