# ADR 0001 — Temporarily Pydantic-backed domain contracts

- **Status:** Superseded by [ADR 0003](./0003-domain-purity.md)
- **Date:** 2026-04-26
- **Superseded on:** 2026-04-29
- **Deciders:** allanbrunobr
- **Related subphase:** Structural Phase 1 — 1a (PR #2) and 1b (PRs #4, #5, #6)

> **Note (2026-04-29):** ADR 0003 supersedes this decision. Structural Phase 2
> removed `core/models.py` and the shims. Phase 3 moves away from the Pydantic-backed domain
> through edge DTOs in `server/webhook/schemas.py` and pure dataclasses in `domain/`. The
> content below is kept as history. Any statement that contradicts ADR 0003 is
> outdated.

## Context

Phase 1 of the structural refactor moved the shared contracts of the AIOps pipeline from
`core/models.py` to the `domain/` layer. It kept `core/models.py` as an explicit shim
to preserve the ~50 existing import sites. The relevant types today are:

| Contract | Current location | Base type |
|---|---|---|
| `LogEntry`, `MetricPoint`, `Span`, `SimulatedIncident`, `PredictionReport`, `MetricSeries` | `domain/incident.py` | pure `@dataclass` |
| `MitigationAction` | `domain/incident.py` | `dict` subclass with `__getattr__/__setattr__` |
| `NormalizedAlert` | `domain/alert.py` | `pydantic.BaseModel` (`frozen=True`) |
| `IncidentState` | `domain/incident_state.py` | `pydantic.BaseModel` (`populate_by_name=True`) |

The dependency rule of the `domain/` layer (documented in `domain/__init__.py`) is
one-way: other layers can import from `domain/`, but `domain/` imports nothing else
from the project. This rule is already respected.

The point that is **not** respected is the "pure Python (stdlib): no Pydantic" rule.
`NormalizedAlert` and `IncidentState` were moved with `BaseModel` kept, as a pragmatic
choice. Converting them now would require a broad refactor of the call sites.

## Decision

Keep `NormalizedAlert` and `IncidentState` in `domain/` as `pydantic.BaseModel` for
now. Do **not** convert them to dataclasses as part of the current work.

## Consequences

**Positive:**

- The structural migration is complete with no functional regression. Runtime identity is preserved
  (`core.models.X is domain.<mod>.X` for the 4 contracts).
- The 18+ call sites of `state.model_copy(update={...})` in production (`pipeline_nodes`
  ×6, `safety_nodes` ×4, `accuracy.py:78`, `main.py:1872`) keep working without
  change.
- The `severity` validator in `NormalizedAlert` (Pydantic `field_validator`) and the
  JSON round-trip exercised in `tests/test_core.py:95-96` remain intact.
- The HTTP/MCP edge (FastAPI) integrates directly with Pydantic. No additional DTO had
  to be introduced.

**Negative:**

- The `domain/` layer is **not** framework-agnostic yet. Pydantic is an effective
  dependency of the domain.
- The docstring of `domain/__init__.py` states "pure Python (stdlib)". It is
  temporarily out of sync with the content. It must be updated when this
  decision evolves.
- The domain depends on an HTTP framework decision transitively. Replacing
  Pydantic would be a non-local refactor.

**Mitigations:**

- The shims in `core/models.py` keep the historical entry point stable. Any
  future decision to change the internal representation can preserve the
  `from core.models import ...` interface.
- Runtime identity and resolved annotations preserve type behavior
  (Pydantic field types, isinstance, Protocol matching).

## Exit criteria

Revisit this decision when **any** of the items below changes:

1. **`model_copy(update=...)` is replaceable.** Either through `dataclasses.replace` + a helper,
   or through a change in the mutation model (explicit state machine, functional immutable
   updates through a pure function). Today there are 18+ calls in production.
2. **Separate edge DTOs.** Introduction of an `adapters/<x>/schemas.py` layer
   (FastAPI/MCP) that validates/serializes input/output and leaves `domain/` pure. This
   enables the `NormalizedAlert` → dataclass conversion without affecting the HTTP edge.
3. **JSON round-trip preserved.** `tests/test_core.py:95-96` exercises
   `state.model_dump_json()` + `IncidentState.model_validate_json(...)`. Replacing
   Pydantic requires an equivalent (probably through an edge DTO + an explicit
   serializer).
4. **`severity` validation preserved.** `NormalizedAlert.severity` accepts only
   `{critical, error, warning, info}` (case-insensitive, normalizes to lowercase).
   A replacement requires an equivalent validator, ideally at the ingestion point (edge
   DTO) and not in the domain.

The 4 criteria are cumulative: no production call site can break when
the exit happens.

## Review trigger

Re-evaluate this ADR when **any** of the events below occurs:

- The next relevant functional change in `server/webhook/`, `server/routes/a2a.py` or
  `mcp_server/` that needs to touch the shape of the contract (new field, new validator).
- A planned refactor of the incident state machine (the `state` field of
  `IncidentState`). This is a natural moment to introduce explicit mutations and
  potentially move away from `model_copy(update=...)`.
- A new alert source that requires its own validator, different from the current one,
  which strengthens the case for separating the domain entity from the edge DTO.

Until then, the domain stays stable in this intermediate state.

## Notes

- This ADR covers **only** the representation of the contracts in the domain. It does not cover the
  internal organization of `pipeline/`, `agents/`, `blueprint/`, `safety/`, or
  Phase 2 of the structural refactor.
- The decision is deliberately conservative: it prefers stability of the production pipeline
  over short-term architectural purity.
