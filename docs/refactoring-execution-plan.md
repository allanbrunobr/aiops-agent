# Refactoring Execution Plan

## Objective

Finish the architectural refactoring started in Phases 0, 1 and 2a without opening
unnecessary functional changes. The plan below is the direct execution order to bring
the repository to a cleaner, more testable state that aligns with the accepted ADRs.

Assumed current baseline: `main` at `84e0ec8`, PR #9 merged, `LLMClient` introduced
as a test seam and containment boundary per ADR 0002.

## Execution Rules

- One PR per phase. Do not mix file moves with behavioral changes.
- Each PR must pass `scripts/ci-local.sh unit` locally before the push.
- Run the full CI before merging: unit, integration/snapshot and Rust/PyO3.
- Use shims only as a transition. Every new shim needs a removal criterion.
- Do not refactor Pydantic in `domain/` unless the ADR 0001 triggers are met or a
  successor ADR is approved.

## Phase 2b — Close the LLM Seam

**Branch:** `refactor/llm-seam-callers`

**Scope:**

- Migrate `agents/orchestrator.py` to `client.create_message(...)`.
- Migrate `agents/skill_evolver.py` to `client.create_message(...)`.
- Type the constructors against `application.ports.llm_client.LLMClient` where it fits.
- Remove the `messages` property from `adapters/llm/anthropic_client.py`.
- Update the adapter and port docstrings to reflect that `.messages` is no longer a
  public contract.
- Update mocks in `tests/test_orchestrator.py` and tests related to the skill
  evolver.

**Acceptance criteria:**

```bash
rg -n "from anthropic|import anthropic|Anthropic\(" . -g "*.py"
rg -n "\.messages\.create" agents application adapters blueprint main.py -g "*.py"
python -m compileall application adapters agents
python -m pytest tests/test_orchestrator.py tests/test_llm_model_config.py -q
scripts/ci-local.sh unit
```

Expected result: direct Anthropic import only in
`adapters/llm/anthropic_client.py`; no `.messages.create` call outside the adapter.

## Phase 2c — Reporter Port with Structured Events

**Branch:** `refactor/reporter-port`

**Scope:**

- Create `application/ports/reporter.py`.
- Create adapters:
  - `adapters/reporter/rich_console.py`
  - `adapters/reporter/structured_log.py`
- Define a small event vocabulary, for example: `diagnosis_started`,
  `llm_call_failed`, `thought_observed`, `tool_called`, `tool_observed`,
  `diagnosis_completed`, `mitigation_proposed`, `human_approval_requested`.
- Migrate `agents/detector.py`, `agents/diagnoser.py` and
  `agents/mitigator.py` first.
- Keep `main.py` with Rich for now. The CLI is an interface, not agent domain.

**Acceptance criteria:**

```bash
rg -n "from rich|Console\(|Panel\(|console\.print" agents -g "*.py"
python -m compileall application adapters agents
python -m pytest tests/test_diagnose_incident.py tests/test_tool_methods.py tests/test_safety.py -q
scripts/ci-local.sh unit
```

Expected result: agents do not import Rich directly; visual output lives in an adapter.

## Phase 2d — Ports and Persistence

**Branch:** `refactor/incident-store-port`

**Scope:**

- Move `IncidentStore` from `core/protocols.py` to
  `application/ports/incident_store.py`.
- Move `InMemoryStore` from `core/store.py` to
  `adapters/persistence/in_memory_store.py`.
- Create `adapters/persistence/__init__.py`.
- Keep `core/protocols.py` and `core/store.py` as temporary shims with `__all__`.
- Update imports in production code touched by the change.

**Acceptance criteria:**

```bash
rg -n "from core\.protocols|from core\.store|import core\.protocols|import core\.store" .
python -m compileall application adapters core blueprint server mcp_server
python -m pytest tests/test_core.py tests/test_engine.py tests/test_api/test_accuracy.py -q
scripts/ci-local.sh unit
```

Expected result: the concrete store implementation leaves `core/`; application
contracts live in `application/ports/`.

## Phase 2e — Retire Legacy Shims

**Branch:** `refactor/remove-core-shims`

**Scope:**

- Update the remaining imports:
  - `core.models` -> `domain.incident`, `domain.alert`, `domain.incident_state`
  - `core.protocols` -> `application.ports.incident_store`
  - `core.store` -> `adapters.persistence.in_memory_store`
- Remove the shims if there is no external public contract to preserve.
- If there is a risk of external breakage, keep the shims for one version with
  `DeprecationWarning` and open a removal issue.

**Acceptance criteria:**

```bash
rg -n "from core\.models|import core\.models|from core\.protocols|from core\.store" .
python -m compileall .
scripts/ci-local.sh unit
scripts/ci-local.sh integration
```

Expected result: `core/` stops being a pass-through point for new contracts.

## Phase 3 — Domain Purity and Edge DTOs

**Initial branch:** `docs/adr-0003-domain-purity`

This phase depends on ADR 0001. Before any code, write ADR 0003 to decide the exit from
the Pydantic-backed domain.

**ADR 0003 must decide:**

- Where the Pydantic DTOs for webhook, HTTP, MCP and A2A live.
- How to replace `IncidentState.model_copy(update=...)`.
- How to preserve the JSON round-trip that `model_dump_json()` and
  `model_validate_json(...)` cover today.
- How to preserve the `NormalizedAlert.severity` validation.
- How to turn `MitigationAction` from a `dict subclass` into an explicit contract.

**Execution after the ADR is accepted:**

- Create Pydantic DTOs in `server/webhook/schemas.py` and/or
  `interfaces/*/schemas.py`.
- Convert `NormalizedAlert` to a pure dataclass in the domain.
- Convert `IncidentState` to a pure dataclass or an explicit state machine object.
- Create explicit JSON serializers.
- Convert `MitigationAction` to a dataclass with `to_dict()`/`from_dict()`.
- Update the call sites of `model_copy`, `model_dump_json`, `model_validate_json`,
  `dict(action)` and `action["field"]`.

**Acceptance criteria:**

```bash
rg -n "pydantic|BaseModel|ConfigDict|Field|field_validator" domain -g "*.py"
rg -n "model_copy|model_dump|model_validate|dict\(action\)|action\[" .
python -m pytest tests/test_core.py tests/epic2 tests/test_validate_action.py -q
scripts/ci-local.sh unit
scripts/ci-local.sh integration
```

Expected result: `domain/` does not depend on Pydantic; validation/serialization live at
the edges.

## Phase 4 — Interface and Use Case Reorganization

**Branch:** `refactor/interface-layout`

Run this only after the previous phases are green. This phase is structural and
must use `git mv` in commits separate from the import updates.

**Recommended scope:**

- `server/` -> `interfaces/http/`
- `mcp_server/` -> `interfaces/mcp/`
- `demo/` -> `interfaces/demo/`
- `main.py` -> `interfaces/cli/main.py`, or keep `main.py` as a thin entrypoint that
  delegates to `interfaces/cli/`.
- Evaluate `agents/`, `pipeline/`, `safety/`, `rag/` as `application/use_cases/`
  only if there is a concrete benefit. Do not do cosmetic renaming without reducing
  coupling.

**Acceptance criteria:**

```bash
python -m compileall interfaces application adapters domain
scripts/ci-local.sh unit
scripts/ci-local.sh integration
python main.py --no-llm --no-rag
```

Expected result: entrypoints are grouped as interfaces; application rules and
adapters stay independent of the input framework.

## Phase 5 — Composition Builders

**Branch:** `refactor/composition-builders`

**Scope:**

- Create `application/builders.py` or `app/builders.py` for shared subgraphs.
- Extract pure builders:
  - `build_diagnoser_agent(...)`
  - `build_detector_agent(...)`
  - `build_pipeline(...)`
  - `build_rag(...)`
- Keep each entrypoint with its own composition root: CLI, HTTP, MCP and demo.
- Do not centralize HTTP middleware, CLI parsing, MCP handshake or demo assets.

**Acceptance criteria:**

```bash
rg -n "build_.*agent|build_pipeline|build_rag" application app interfaces main.py server mcp_server demo
python -m compileall .
scripts/ci-local.sh unit
scripts/ci-local.sh integration
```

Expected result: duplicated wiring leaves the entrypoints, but each interface keeps its
top-level responsibility.

## Final Checklist

Run this when all the phases above are merged:

```bash
git switch main
git pull origin main
scripts/ci-local.sh unit
scripts/ci-local.sh integration
scripts/ci-local.sh rust
rg -n "from anthropic|import anthropic|Anthropic\(" . -g "*.py"
rg -n "\.messages\.create" . -g "*.py"
rg -n "from rich|Console\(|Panel\(|console\.print" agents -g "*.py"
rg -n "from core\.models|from core\.protocols|from core\.store" .
rg -n "pydantic|BaseModel|ConfigDict|Field|field_validator" domain -g "*.py"
python main.py --no-llm --no-rag
```

## Definition of Done

The refactoring is considered finished when:

- The Anthropic SDK is contained in `adapters/llm/`.
- LLM call sites use `LLMClient.create_message()`.
- Agents do not depend on Rich.
- Ports live in `application/ports/`.
- Concrete implementations live in `adapters/`.
- Legacy shims were removed or have an explicit removal date/issue.
- `domain/` is pure Python, or an accepted ADR justifies a remaining exception.
- The full CI is green on `main`.
