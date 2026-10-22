# ADR 0003 — Exit from the Pydantic-backed domain through edge DTOs and pure dataclasses

- **Status:** Accepted
- **Date:** 2026-04-29
- **Deciders:** allanbrunobr
- **Supersedes:** ADR 0001
- **Related subphase:** Structural Phase 3 — Domain Purity and Edge DTOs

## Context

ADR 0001 left `NormalizedAlert` and `IncidentState` in `domain/` as
`pydantic.BaseModel` as a pragmatic choice, with 4 cumulative exit criteria
and explicit review triggers. Structural Phase 2 (PRs #11, #12, #13) delivered
`adapters/persistence/`, `application/ports/`, `adapters/llm/anthropic_client.py` and
`server/cli.py`, and removed the `core/models.py` and `core/store.py` shims.

`core/protocols.py` was reduced to `CredentialProvider`. The domain is topologically
stable, but it still depends on Pydantic. The "pure Python (stdlib)" condition promised
in the docstring of `domain/__init__.py` is not true yet.

### Current state in `main` (objective reference)

| Contract | Location | Base type | Remarks |
|---|---|---|---|
| `LogEntry`, `MetricPoint`, `Span`, `SimulatedIncident`, `PredictionReport`, `MetricSeries` | `domain/incident.py` | pure `@dataclass` | Nothing to do — already stdlib |
| `MitigationAction` | `domain/incident.py` | `dict` subclass with `__getattr__/__setattr__` | Only contract with a dual attribute+key contract |
| `NormalizedAlert` | `domain/alert.py` | `pydantic.BaseModel(frozen=True)` + `field_validator("severity")` | The severity validator must move to the edge |
| `IncidentState` | `domain/incident_state.py` | `pydantic.BaseModel(populate_by_name=True)` | Source of the 11 `state.model_copy(update={...})` |

### Production sites that reference Pydantic

- `state.model_copy(update={...})` — 11 call sites, mapped:
  - `main.py:1880`
  - `server/api/accuracy.py:78`
  - `blueprint/adapters/safety_nodes.py:73, 129, 195, 283`
  - `blueprint/adapters/pipeline_nodes.py:48, 90, 125, 147, 158, 200`
- `model_dump_json()` / `model_validate_json(...)` — exercised in `tests/test_core.py:72-74`
  (NormalizedAlert) and `tests/test_core.py:97-100` (IncidentState).
- `field_validator("severity")` — `domain/alert.py`, accepts `{critical, error, warning, info}`
  case-insensitive and normalizes to lowercase.
- `MitigationAction` — re-exported by `safety/tnr.py:21`. The snapshots
  `tests/snapshots/{full_pipeline_result, memory_leak_mitigation, metric_anomalies, mitigation, safety_validation, unknown_fault_mitigation}.json`
  contain the fields `description`, `command`, `risk_level` (and `rollback_command` when defined)
  in a wire format that **cannot change** — `tests/snapshots/` is invariant per CLAUDE.md.

### ADR 0001 exit criteria — status

1. **`model_copy(update=...)` is replaceable.** OK — `dataclasses.replace(state, **kwargs)`
   is a direct equivalent, stdlib, with no type cost. The 11 sites are a mechanical rewrite.
2. **Separate edge DTOs.** Pending. `server/webhook/` validates through `NormalizedAlert`
   directly. It must migrate to a Pydantic `server/webhook/schemas.py` and convert to the
   domain dataclass at ingestion.
3. **JSON round-trip preserved.** The tests in `tests/test_core.py` exercise Pydantic.
   They must migrate to an explicit serializer (stdlib `dataclasses.asdict()` +
   `json.dumps()` with a `datetime` encoder) or run through the edge DTO.
4. **`severity` validation preserved.** It must migrate to the edge — Pydantic DTO
   (`server/webhook/schemas.py`) and/or normalizers (`server/webhook/normalizers/*`).

The 4 criteria can be addressed within the scope of Phase 3. None requires a change outside the
domain + HTTP edge + tests.

## Decision

Exit from Pydantic in the domain with the following decomposition:

1. **Pydantic DTOs live in `server/webhook/schemas.py`.** This is the canonical edge for
   ingestion of external alerts (PagerDuty, Grafana, Databricks). FastAPI still
   integrates directly with Pydantic.

   **MCP and A2A do not get their own DTOs in this phase.** Evidence audit (2026-04-29):

   | Channel | Path | Builds domain entities directly? |
   |---|---|---|
   | MCP — primary | `mcp_server/tools.py:759` (`normalizer_registry.normalize(...)`) | No. Reuses `server/webhook/normalizers/*`. |
   | MCP — fallback | `mcp_server/tools.py:765-781` | **Yes.** Builds `NormalizedAlert(...)` with an inline severity map (line 767-768). |
   | A2A | `server/routes/a2a.py:131-168` | **Yes.** Builds `NormalizedAlert(...)` (line 150) and `IncidentState(...)` (line 162) directly, with an inline `_map_severity` (line 131). |

   Although A2A and the MCP fallback build domain entities at the edge, **Phase 3
   does not introduce dedicated Pydantic DTOs for these channels**. Reasons:

   - **Mechanical change through PR B/PR C.** When PR B changes the constructor of
     `NormalizedAlert` (`BaseModel` → `@dataclass(frozen=True)`) and PR C changes
     `IncidentState`, the 3 sites above (`mcp_server/tools.py:770`, `server/routes/a2a.py:150`,
     `:162`) get a mechanical syntax adjustment in the same PR. There is no additional DTO
     work to do — only an update to how they instantiate.
   - **Severity is already normalized by inline maps.** MCP fallback (`_severity_map`) and A2A
     (`_map_severity`) already convert to the canonical vocabulary before they build.
     The Pydantic validation of `field_validator("severity")` is redundant with these maps.
     When the validator leaves `domain/alert.py` (PR B), MCP and A2A keep
     normalizing locally with no loss of guarantee.
   - **Separate DTOs would be premature.** Today MCP and A2A produce `NormalizedAlert`
     1:1. Creating `MCPAlertDTO` and `A2AAlertDTO` would add 2 Pydantic schemas while
     no field diverges from the HTTP contract. This meets exit criterion #1 of this ADR
     ("multiple channels diverge in DTO shape") — when this is no longer true, a
     successor ADR introduces specific DTOs.

   **Scope consequence:** PR B includes the update of the 2 `NormalizedAlert` sites
   in `mcp_server/tools.py:770` and `server/routes/a2a.py:150`. PR C includes the update
   of the `IncidentState` site in `server/routes/a2a.py:162`. There is no additional PR.

2. **`MitigationAction` becomes `@dataclass(frozen=True)` in the domain.** Preserved fields:
   `description: str`, `command: str`, `risk_level: str`, `rollback_command: str | None`.
   `dataclasses.asdict(action)` must produce a wire format identical to the current
   `dict(action)` to preserve the 6 affected snapshots. Key access
   (`action["field"]`) is no longer supported — call sites migrate to attribute
   access (`action.field`). `safety/tnr.py:21` keeps the re-export.

3. **`NormalizedAlert` becomes `@dataclass(frozen=True)`.** The `field_validator("severity")`
   moves to `server/webhook/schemas.py` (Pydantic DTO at the HTTP edge) and to the
   normalizers in `server/webhook/normalizers/*` (PagerDuty, Grafana, Databricks).
   In the domain, `NormalizedAlert` trusts that it arrived normalized — any
   programmatic construction (tests, fixtures) that needs validation uses the edge DTO.

4. **`IncidentState` becomes `@dataclass`.** Not frozen — the pipeline always returned a new
   state through `model_copy`, but the object itself does not need to be immutable, and mutability
   helps with tooling (debugger, REPL). The 11 call sites of `state.model_copy(update=...)`
   migrate directly to `dataclasses.replace(state, **kwargs)`. Do **not** introduce a
   `.replace()` method on `IncidentState` — `dataclasses.replace` is the idiomatic stdlib form and
   avoids reinventing an API that already exists.

5. **JSON round-trip through an explicit serializer.** Replace `model_dump_json()` /
   `model_validate_json(...)` in tests with a stdlib serializer (`dataclasses.asdict` +
   `json.dumps` with an ISO-8601 encoder for `datetime`, and `json.loads` + a factory function
   in the reverse path). Production paths that serialize to HTTP use the edge Pydantic
   DTO — not the domain.

### Implementation plan — 3 atomic PRs

The order follows increasing risk. Each PR is independent and bisectable.

**PR A — `MitigationAction` → frozen dataclass.** Minimal risk (few call sites, no
HTTP edge affected).

- `domain/incident.py`: replaces the `dict subclass` with `@dataclass(frozen=True)`.
- Migrates call sites that use `action["field"]` to `action.field` (audit with
  `grep -rn 'action\[' --include="*.py"`).
- Verifies that `dataclasses.asdict(MitigationAction(...))` produces a wire format identical
  to the 6 affected snapshots. Re-runs `pytest tests/ -v` — snapshots **cannot** change.

**PR B — `NormalizedAlert` → frozen dataclass + edge DTO.** Medium risk (touches the
HTTP edge, MCP, A2A).

- Creates `server/webhook/schemas.py` with `NormalizedAlertDTO(BaseModel, frozen=True)`
  that contains the migrated `field_validator("severity")`.
- `domain/alert.py`: replaces `BaseModel` with `@dataclass(frozen=True)`. Removes
  `field_validator`.
- Updates `server/webhook/normalizers/{pagerduty,grafana,databricks}.py` to build
  the DTO first, then convert to `NormalizedAlert` (dataclass) at the output of the
  normalizer.
- Updates the direct construction sites of `NormalizedAlert` outside `server/webhook/`:
  `mcp_server/tools.py:770` (MCP fallback) and `server/routes/a2a.py:150` (A2A handler).
  Both keep building the domain entity directly — only the syntax changes
  (`@dataclass(frozen=True)` instead of `BaseModel`).
- Migrates `tests/test_core.py:72-74` to an explicit serializer or to a test of the edge
  DTO.

**PR C — `IncidentState` → dataclass + 11 `model_copy` call sites.** Highest risk
(touches pipeline + safety + main + A2A).

- `domain/incident_state.py`: replaces `BaseModel` with `@dataclass`. Removes
  `model_config`. `Field(default_factory=...)` becomes the stdlib `field(default_factory=...)`.
- Migrates the 11 sites of `state.model_copy(update={...})` to
  `dataclasses.replace(state, **kwargs)`. Exact list in "Current state" above — do it
  in a separate commit inside the PR to make review easier.
- Updates the direct construction site of `IncidentState` outside the pipeline:
  `server/routes/a2a.py:162` (A2A handler). It keeps building directly — only the
  syntax changes.
- Migrates `tests/test_core.py:97-100` to an explicit serializer.
- Updates the `domain/incident_state.py` docstring and removes the "temporarily
  Pydantic-backed" note.
- Updates `domain/__init__.py` and removes the exception about Pydantic.

## Consequences

**Positive:**

- `domain/` no longer depends on Pydantic. The criterion `rg "pydantic|BaseModel|ConfigDict|Field|field_validator" domain` returns 0.
- Validation is concentrated at the edge: severity is validated once at ingestion (DTO or
  normalizer), never in the internal flow. This reduces the chance of double validation with
  divergent messages.
- Functional mutations through `dataclasses.replace` are idiomatic stdlib — a reader does not
  need to know Pydantic to understand the pipeline.
- Domain unit tests run without Pydantic installed (relevant for
  `requirements-test-unit.txt`).
- ADR 0001 leaves the "Accepted but with a pending exit criterion" state. The domain becomes
  architecturally consistent with the docstring of `domain/__init__.py`.

**Negative:**

- The refactor of 11 `model_copy` call sites is mechanical but has a large surface (it touches
  `pipeline_nodes`, `safety_nodes`, `accuracy.py`, `main.py`). There is a regression risk if
  a site uses implicit Pydantic-specific behavior (e.g. validation in `update={...}`).
  Mitigation: a dedicated PR C, with the full snapshots and CI as a safety net.
- DTO duplicated between `server/webhook/schemas.py` (Pydantic) and `domain/alert.py`
  (dataclass) — two names for the same concept (`NormalizedAlertDTO` vs.
  `NormalizedAlert`). This is the cost of domain purity. Mitigation: a docstring in the DTO
  that points to the domain entity, and vice versa.
- `MitigationAction` loses key access (`action["risk_level"]`). Old call sites
  that depended on `dict(action)` to serialize must use `dataclasses.asdict`.
  Audit with grep before PR A.

**Mitigations:**

- Each PR is atomic and bisectable, with green CI before merge.
- The wire format of the snapshots is invariant — any change in the `dataclasses.asdict`
  output breaks `pytest tests/test_*snapshot*.py` immediately.
- ADR 0002 is not affected: `LLMClient` stays a passthrough, `agents/diagnoser.py`
  still parses JSON from the Anthropic SDK and builds `MitigationAction` —
  only the constructor changes (`MitigationAction(description=..., ...)` instead of
  `MitigationAction(description=..., ...)` that internally is `dict.__init__`).

## Exit criteria

Revisit this decision when **any** of the items below occurs:

1. **Multiple ingestion channels diverge in DTO shape.** Today PagerDuty, Grafana and
   Databricks share a single `NormalizedAlert`. If A2A or MCP need their own DTOs
   that cannot be mapped to `NormalizedAlert`, revisit this decision
   to discuss multiple DTOs for the same domain entity.
2. **Validation in the domain becomes necessary again.** If a case appears where the domain
   must reject invalid input (e.g. pipeline state machine invariants),
   `dataclass` + factory function may not be enough, and `attrs` or manual
   validators may need to come in.
3. **`dataclasses.replace` performance becomes a bottleneck.** Unlikely given the current
   frequency (1 time per node, ~8 nodes per incident), but if the pipeline grows to
   hundreds of mutations per run, consider explicit in-place mutation.
4. **Pydantic DTOs explode in complexity.** If `server/webhook/schemas.py` grows to
   100+ lines with conditional logic, it is a signal to extract `interfaces/http/schemas/`
   or similar. To be addressed by a successor ADR about the interfaces layout.

## Review trigger

Re-evaluate this ADR when:

- A new alert source requires its own DTO that does not map 1:1 to `NormalizedAlert`.
- A refactor of the `IncidentState` state machine (`state` field) introduces
  invariants that `dataclass` cannot express cheaply.
- `attrs`, `msgspec` or another modeling library is added to the stack — a natural moment
  to revisit the pure stdlib choice.
- Phase 4 (interfaces reorganization) moves `server/webhook/` to
  `interfaces/http/` — schemas follow the move.

## Notes

- This ADR **supersedes** ADR 0001. The ADR 0001 entry must be marked
  `Status: Superseded by ADR 0003` when this document is merged.
- ADR 0002 (`LLMClient`) **is not affected**. It stays a passthrough. Its interaction with
  Pydantic was nil — the port always returned Anthropic-shaped objects.
- `tests/snapshots/` is invariant per CLAUDE.md. PR A must prove empirically
  (by running the tests) that the wire format of `MitigationAction` does not change. If it changes,
  PR A does not merge — the alternative is to keep `MitigationAction` as a `dict subclass` and
  document the exception, but that contradicts the decision.
- The Phase 3 acceptance criteria of the execution plan are met by this ADR + 3
  atomic PRs. Local CI (`scripts/ci-local.sh unit` + `integration`) must run green
  at the end of each PR.
- The docstring of `domain/__init__.py` that states "pure Python (stdlib): no Pydantic"
  reflects reality at the end of PR C.
