# ADR 0002 — `LLMClient` como seam de teste e containment boundary

- **Status:** Accepted
- **Data:** 2026-04-26
- **Decisores:** allanbrunobr
- **Relacionado:** ADR 0001, plano original da Fase 2a (spike `refactor/fase-2a-llm-port`, revertido)

## Contexto

A Fase 1 fechou contratos do domínio em `domain/`. Para a Fase 2 foi proposto extrair
uma porta `LLMClient` cobrindo o uso atual do SDK Anthropic. Um spike no branch
`refactor/fase-2a-llm-port` introduziu:

- `domain/ports/llm_client.py` — `Protocol` com `create_message(**kwargs: Any) -> Any`
- `adapters/llm/anthropic_client.py` — `AnthropicLLMClient` envolvendo `anthropic.Anthropic`
- Modificações em `agents/diagnoser.py` removendo o `import anthropic` direto

O spike foi revertido após revisão por equipe de agentes que apontou três problemas
estruturais:

1. A porta proposta é passthrough (`**kwargs: Any` → `Any`). Não abstrai nem o payload
   (kwargs aceita dialeto Anthropic — `max_tokens`, `system`, `tools` com `input_schema`)
   nem a resposta (callers consomem `response.content[i].type`, `block.text`,
   `tc.id/name/input`).
2. A localização em `domain/ports/` é incorreta: `LLMClient` é dependência de serviço
   de aplicação, não de entidade pura. Lugar apropriado é `application/ports/`
   (a ser criado).
3. Sem decisão explícita sobre o que a porta promete, há risco de espalhar uma
   abstração frágil pelos call sites existentes e ficar com mockabilidade vendida
   como hexagonal.

### Estado atual em `main` (referência objetiva)

| Arquivo | Importa SDK direto? | Instancia `Anthropic()`? | Usa `client.messages.create(...)`? |
|---|---|---|---|
| `main.py:502, 505` | sim | sim | não |
| `blueprint/adapters/pipeline_nodes.py:117` | sim | sim | não |
| `agents/diagnoser.py:19, 101` | sim | sim (com fallback via env) | sim, 2 call sites (`_call_llm`, `reflect`) |
| `agents/orchestrator.py:247` | não — recebe via DI | não | sim, 5 call sites |
| `agents/skill_evolver.py:47, 82` | não — recebe via DI | não | sim, 1 call site |

`diagnoser.py`, `orchestrator.py` e `skill_evolver.py` somam **8 call sites** que
consomem **diretamente o shape Anthropic da resposta** — campos efetivamente
referenciados hoje: `response.content[*].type`, `block.text`, `tc.id`, `tc.name`,
`tc.input`. Isso é o acoplamento real — não os imports do SDK.

## Decisão

`LLMClient` será introduzida como **seam de teste e containment boundary**, **não**
como abstração para provider-swap.

**O que a porta promete:**

- Substituir `Anthropic()` direto nos instanciadores (`main.py`, `pipeline_nodes.py`,
  `diagnoser.py` fallback) por uma única factory que devolve um adapter.
- Permitir testes sem monkey-patch do SDK Anthropic. Fixtures continuam devolvendo
  objetos Anthropic-shaped (a porta é passthrough), mas deixam de depender do
  import direto do SDK e do `client.messages.create` real.
- Centralizar políticas transversais futuras (timeout, retry, métricas, rate limit)
  em um único lugar — o adapter.

**O que a porta NÃO promete:**

- Substituição de provider em runtime ou build time.
- Tipagem own-domain de `Message`, `ToolDef`, `ToolCall`, `ChatResponse`, `TextBlock`,
  `ToolUseBlock`. Esses tipos continuam sendo os do SDK Anthropic, vazando pela porta.
- Kwargs normalizados (`max_tokens` vs. `max_completion_tokens`, schema de tools, etc.).

**Containment boundary** significa: nenhum arquivo do projeto fora de
`adapters/llm/anthropic_client.py` pode importar `anthropic` direto. Imports diretos
em `main.py`, `pipeline_nodes.py` e `diagnoser.py` devem migrar para o adapter. Mas o
shape consumido pelos callers (estrutura da resposta) permanece Anthropic-específico
até gatilho de saída.

## Consequências

**Positivas:**

- Refactor incremental viável em PRs pequenos. Cada arquivo migra isoladamente.
- Custo upfront baixo. Sem desenho de tipos novos. Sem retrofit em testes que mockam
  `client.messages.create`.
- Mockabilidade ganhada (testes podem usar `LLMClient` Protocol em vez de monkey-patch
  do SDK).
- Lugar único para retry/timeout/métricas quando precisar.
- Alinhamento com decisão pragmática do ADR 0001 (camadas "domain-ish" hoje).

**Negativas:**

- A porta vaza dialeto Anthropic. Adicionar provider real (OpenAI, Bedrock, vLLM)
  exigirá ADR sucessor com retipagem completa de payload e resposta.
- "Porta" é nome forte para o que efetivamente é seam — risco de leitor futuro
  interpretar como abstração e tomar decisões sobre essa premissa errada. Mitigação:
  docstring explícita no `LLMClient` Protocol e no adapter.
- `agents/diagnoser.py`, `orchestrator.py` e `skill_evolver.py` continuam acoplados ao
  shape da resposta Anthropic. Refactor deles fica fora desta fase.

**Mitigações:**

- Docstring clara em `LLMClient` registrando "containment boundary, não provider-swap".
- Adapter expõe assinatura passthrough `create_message(**kwargs) -> Any` mas o
  retorno permanece tipado como `anthropic.types.Message` na mente do caller.
- Lint/CI rule futura (não obrigatória nesta fase): bloquear `import anthropic` fora
  de `adapters/llm/`.

## Critérios de saída (para virar provider-swap real)

Esta decisão deve ser revisitada quando **qualquer um** dos itens abaixo ocorrer:

1. **Aparece um segundo provider concreto.** OpenAI, Anthropic Bedrock, vLLM, ou
   modelo self-hosted entram em uso real (não experimental).
2. **Necessidade de testar side-by-side.** Avaliação comparativa entre providers
   exige a mesma lógica de agente rodando contra implementações diferentes.
3. **Refactor planejado de tool-calling.** Mudança de schema de tools que toque
   múltiplos call sites — momento natural para tipar `ToolDef`, `ToolCall`,
   `ToolUseBlock` no domínio.
4. **Shape Anthropic precisa se espalhar para novos módulos.** Quando *novos* campos
   provider-specific (além dos já consumidos hoje: `content[*].type`, `block.text`,
   `tc.id/name/input`) tiverem que ser referenciados em **mais de um módulo novo**
   fora de `adapters/llm/` — ou quando um módulo novo precisar tocar o shape pela
   primeira vez. O critério é incremental sobre o estado atual, não absoluto: hoje
   3 módulos já tocam o shape e a decisão é deliberadamente conviver com isso.

A saída exige ADR sucessor (0003+) cobrindo:

- Tipos own-domain: `Message`, `Role`, `ToolDef`, `ToolCall`, `ChatResponse`,
  `TextBlock`, `ToolUseBlock`, `StopReason`, `Usage`.
- Normalização de kwargs (modelo, max_tokens, system prompt, tools).
- Plano de migração dos call sites em `diagnoser.py`, `orchestrator.py`,
  `skill_evolver.py`.
- Estratégia de testes side-by-side, se aplicável.

## Gatilho para revisão

- Adição de provider real (não experimental).
- Necessidade concreta de testes ou rollouts side-by-side.
- Mudança em tool-calling que toque múltiplos call sites.
- Decisão de produto sobre flexibilidade de modelo/provider.

## Notas

- Esta decisão **não** cobre a porta `Reporter` (Rich → eventos estruturados). Será
  ADR separado quando o trabalho começar.
- Esta decisão **não** cobre a reorganização de `core/protocols.py` →
  `application/ports/` ou `core/store.py` → `adapters/persistence/`. Esses movimentos
  vêm depois das portas estabilizadas, conforme reordenação do plano original.
- `MitigationAction` permanece como `dict subclass` (ADR 0001 ponto 6 do follow-up).
  Não é afetado por esta decisão **porque** a porta é passthrough — o adapter LLM
  devolve `anthropic.types.Message` cru e o `DiagnosisAgent` continua parseando JSON
  para construir `MitigationAction`.
- O spike `refactor/fase-2a-llm-port` foi revertido. A branch pode ser deletada após
  o merge deste ADR.
