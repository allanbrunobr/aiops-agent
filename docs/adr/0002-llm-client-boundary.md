# ADR 0002 — `LLMClient` as a test seam and containment boundary

- **Status:** Accepted
- **Date:** 2026-04-26
- **Deciders:** allanbrunobr
- **Related:** ADR 0001, original Phase 2a plan (spike `refactor/fase-2a-llm-port`, reverted)

## Context

Phase 1 closed the domain contracts in `domain/`. For Phase 2, the proposal was to extract
an `LLMClient` port that covers the current use of the Anthropic SDK. A spike on the
`refactor/fase-2a-llm-port` branch introduced:

- `domain/ports/llm_client.py` — `Protocol` with `create_message(**kwargs: Any) -> Any`
- `adapters/llm/anthropic_client.py` — `AnthropicLLMClient` wrapping `anthropic.Anthropic`
- Changes in `agents/diagnoser.py` that removed the direct `import anthropic`

The spike was reverted after a review by a team of agents that found three structural
problems:

1. The proposed port is a passthrough (`**kwargs: Any` → `Any`). It abstracts neither the payload
   (kwargs accepts the Anthropic dialect — `max_tokens`, `system`, `tools` with `input_schema`)
   nor the response (callers consume `response.content[i].type`, `block.text`,
   `tc.id/name/input`).
2. The location in `domain/ports/` is wrong: `LLMClient` is a dependency of an application
   service, not of a pure entity. The correct place is `application/ports/`
   (to be created).
3. Without an explicit decision on what the port promises, there is a risk of spreading a
   fragile abstraction across the existing call sites and ending up with mockability sold
   as hexagonal.

### Current state in `main` (objective reference)

| File | Imports SDK directly? | Instantiates `Anthropic()`? | Uses `client.messages.create(...)`? |
|---|---|---|---|
| `main.py:502, 505` | yes | yes | no |
| `blueprint/adapters/pipeline_nodes.py:117` | yes | yes | no |
| `agents/diagnoser.py:19, 101` | yes | yes (with fallback via env) | yes, 2 call sites (`_call_llm`, `reflect`) |
| `agents/orchestrator.py:247` | no — receives it via DI | no | yes, 5 call sites |
| `agents/skill_evolver.py:47, 82` | no — receives it via DI | no | yes, 1 call site |

`diagnoser.py`, `orchestrator.py` and `skill_evolver.py` add up to **8 call sites** that
consume **the Anthropic response shape directly**. The fields actually
referenced today are: `response.content[*].type`, `block.text`, `tc.id`, `tc.name`,
`tc.input`. This is the real coupling — not the SDK imports.

## Decision

`LLMClient` will be introduced as a **test seam and containment boundary**, **not**
as an abstraction for provider swap.

**What the port promises:**

- Replace direct `Anthropic()` in the instantiators (`main.py`, `pipeline_nodes.py`,
  `diagnoser.py` fallback) with a single factory that returns an adapter.
- Allow tests without monkey-patching the Anthropic SDK. Fixtures still return
  Anthropic-shaped objects (the port is a passthrough), but they no longer depend on the
  direct SDK import or on the real `client.messages.create`.
- Centralize future cross-cutting policies (timeout, retry, metrics, rate limit)
  in a single place — the adapter.

**What the port does NOT promise:**

- Provider replacement at runtime or build time.
- Own-domain typing of `Message`, `ToolDef`, `ToolCall`, `ChatResponse`, `TextBlock`,
  `ToolUseBlock`. These types remain the Anthropic SDK types and leak through the port.
- Normalized kwargs (`max_tokens` vs. `max_completion_tokens`, tools schema, etc.).

**Containment boundary** means: no project file outside
`adapters/llm/anthropic_client.py` can import `anthropic` directly. Direct imports
in `main.py`, `pipeline_nodes.py` and `diagnoser.py` must migrate to the adapter. But the
shape that the callers consume (the response structure) stays Anthropic-specific
until an exit trigger.

## Consequences

**Positive:**

- An incremental refactor in small PRs is viable. Each file migrates in isolation.
- Low upfront cost. No design of new types. No retrofit of tests that mock
  `client.messages.create`.
- Mockability gained (tests can use the `LLMClient` Protocol instead of monkey-patching
  the SDK).
- A single place for retry/timeout/metrics when needed.
- Alignment with the pragmatic decision of ADR 0001 ("domain-ish" layers today).

**Negative:**

- The port leaks the Anthropic dialect. Adding a real provider (OpenAI, Bedrock, vLLM)
  will require a successor ADR with a complete retyping of payload and response.
- "Port" is a strong name for what is effectively a seam. A future reader can
  interpret it as an abstraction and make decisions based on that wrong premise. Mitigation:
  an explicit docstring in the `LLMClient` Protocol and in the adapter.
- `agents/diagnoser.py`, `orchestrator.py` and `skill_evolver.py` stay coupled to the
  Anthropic response shape. Their refactor is out of scope for this phase.

**Mitigations:**

- A clear docstring in `LLMClient` that records "containment boundary, not provider-swap".
- The adapter exposes the passthrough signature `create_message(**kwargs) -> Any`, but the
  return value stays typed as `anthropic.types.Message` in the caller's mind.
- A future lint/CI rule (not mandatory in this phase): block `import anthropic` outside
  `adapters/llm/`.

## Exit criteria (to become a real provider swap)

Revisit this decision when **any** of the items below occurs:

1. **A second concrete provider appears.** OpenAI, Anthropic Bedrock, vLLM, or a
   self-hosted model goes into real use (not experimental).
2. **Need for side-by-side testing.** A comparative evaluation between providers
   requires the same agent logic to run against different implementations.
3. **Planned tool-calling refactor.** A change to the tools schema that touches
   multiple call sites. This is a natural moment to type `ToolDef`, `ToolCall`,
   `ToolUseBlock` in the domain.
4. **The Anthropic shape needs to spread to new modules.** When *new*
   provider-specific fields (beyond the ones consumed today: `content[*].type`, `block.text`,
   `tc.id/name/input`) must be referenced in **more than one new module**
   outside `adapters/llm/` — or when a new module needs to touch the shape for the
   first time. The criterion is incremental over the current state, not absolute: today
   3 modules already touch the shape, and the decision is to deliberately live with that.

The exit requires a successor ADR (0003+) that covers:

- Own-domain types: `Message`, `Role`, `ToolDef`, `ToolCall`, `ChatResponse`,
  `TextBlock`, `ToolUseBlock`, `StopReason`, `Usage`.
- Normalization of kwargs (model, max_tokens, system prompt, tools).
- A migration plan for the call sites in `diagnoser.py`, `orchestrator.py`,
  `skill_evolver.py`.
- A side-by-side testing strategy, if applicable.

## Review trigger

- Addition of a real provider (not experimental).
- A concrete need for side-by-side tests or rollouts.
- A change in tool-calling that touches multiple call sites.
- A product decision about model/provider flexibility.

## Notes

- This decision does **not** cover the `Reporter` port (Rich → structured events). It will be a
  separate ADR when that work starts.
- This decision does **not** cover the reorganization of `core/protocols.py` →
  `application/ports/` or `core/store.py` → `adapters/persistence/`. These moves
  come after the ports are stabilized, according to the reordering of the original plan.
- `MitigationAction` stays a `dict subclass` (ADR 0001 follow-up item 6).
  This decision does not affect it **because** the port is a passthrough — the LLM adapter
  returns the raw `anthropic.types.Message`, and `DiagnosisAgent` still parses JSON
  to build `MitigationAction`.
- The `refactor/fase-2a-llm-port` spike was reverted. The branch can be deleted after
  this ADR is merged.
