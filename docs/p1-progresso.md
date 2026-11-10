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
| 2 | Gate de comando: metacaracteres de shell, âncora, nome de serviço | F02, T04, SEC-05, SEC-06 | pendente | — |
| 3 | CLI usa o veredito agregado de todas as camadas | F03 | pendente | — |
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
