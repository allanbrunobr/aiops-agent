# AIOps Agent — Internal API Contracts

> No external HTTP API. All interfaces are Python ABCs in `tools/interfaces.py`, designed for future MCP server integration.

---

## Abstract Interfaces (MCP-Ready)

### MetricsSource

| Method | Params | Returns | Description |
|--------|--------|---------|-------------|
| `query()` | `promql: str`, `start: datetime`, `end: datetime`, `step_seconds: int=15` | `list[dict]` | Execute PromQL query, return time series |
| `get_alerts()` | `active_only: bool=True` | `list[dict]` | Return active alerts |

**Future MCP target**: `prometheus-mcp-server`

### LogSource

| Method | Params | Returns | Description |
|--------|--------|---------|-------------|
| `search()` | `query: str`, `service: str|None`, `start: datetime|None`, `end: datetime|None`, `limit: int=100` | `list[dict]` | Search logs by query |
| `tail()` | `service: str`, `lines: int=50` | `list[dict]` | Return last N lines for service |

**Future MCP target**: `datadog-mcp-server`, `grafana-mcp-server`

### TraceSource

| Method | Params | Returns | Description |
|--------|--------|---------|-------------|
| `find_traces()` | `service: str`, `min_duration_ms: float|None`, `start: datetime|None`, `end: datetime|None`, `limit: int=20` | `list[dict]` | Search traces by service and duration |
| `get_trace()` | `trace_id: str` | `dict` | Return complete trace with all spans |

**Future MCP target**: `jaeger-mcp-server`, `grafana-tempo-mcp-server`

### IncidentTracker

| Method | Params | Returns | Description |
|--------|--------|---------|-------------|
| `create_incident()` | `title: str`, `severity: str`, `description: str` | `str` (ID) | Create incident ticket |
| `create_postmortem()` | `incident_id: str`, `timeline: list[dict]`, `root_cause: str`, `action_items: list[str]` | `str` (ID) | Create postmortem document |

**Future MCP target**: `jira-mcp-server`, `pagerduty-mcp-server`

---

## Simulated Implementations

Each ABC has a simulated implementation for the prototype:

| Class | Wraps | Data Source |
|-------|-------|-------------|
| `SimulatedMetricsSource` | `MetricsSource` | `SimulatedIncident.metrics` |
| `SimulatedLogSource` | `LogSource` | `SimulatedIncident.logs` |
| `SimulatedTraceSource` | `TraceSource` | `SimulatedIncident.traces` |

All simulated sources receive data from `data/sample_incident.py:create_incident()` and perform in-memory filtering.

**Note**: `IncidentTracker` has no simulated implementation — it's the only ABC without one. The prototype generates postmortems directly in `main.py:step_postmortem()` instead.

---

## DiagnosisAgent ReAct Tools

The `DiagnosisAgent` exposes 7 tools to the Claude API via tool-use:

| Tool | Input | Output | Purpose |
|------|-------|--------|---------|
| `correlate_signals` | `AggregatedReport` | `str` (plain text) | Cross-reference logs, metrics, traces |
| `reconstruct_timeline` | `AggregatedReport` | `str` (plain text) | Build chronological incident timeline |
| `classify_fault_type` | `AggregatedReport` | `str` (plain text) | Categorize the fault pattern |
| `search_similar_incidents` | `query: str` | `str` (plain text) | RAG search for similar past incidents |
| `get_error_logs` | `service: str` | `str` (plain text) | Retrieve error-level logs for a service |
| `get_metric_anomalies` | `service: str` | `str` (plain text) | Retrieve anomalous metrics for a service |
| `get_slow_traces` | `service: str` | `str` (plain text) | Retrieve slow traces for a service |

**Critical**: All tools return plain text strings, never JSON objects. The LLM wraps its final diagnosis in `<DIAGNOSIS>` XML tags containing JSON.

### Tool Implementation Pattern

Each tool method receives the live `AggregatedReport` object via `self.report` and can call any of its 11 methods (e.g., `to_text()`, `to_compact_text()`). Service-scoped tools (`get_error_logs`, `get_metric_anomalies`, `get_slow_traces`) filter the report's data by the `service` parameter. The `search_similar_incidents` tool delegates to `ObservabilityRetriever.search()` for RAG-backed similarity lookup. When adding new tools, follow this pattern: accept minimal parameters, query the report object, return a plain text string.

---

## Pipeline Stage Contracts

Each `step_*()` function in `main.py` writes to `result["stages"]`:

| Stage Key | Type | Producer | Consumers |
|-----------|------|----------|-----------|
| `detection` | `dict` | `step_agents()` | `step_postmortem()`, `print_summary()` |
| `diagnosis` | `dict` | `step_agents()` | `step_postmortem()`, `print_summary()` |
| `mitigation` | `dict` with `action: MitigationAction` | `step_agents()` | `step_safety_validation()`, `step_remediation_validation()`, `step_postmortem()` |
| `tnr` | `TNRReport` | `step_agents()` | `step_postmortem()`, `print_summary()` |
| `safety` | `SafetyCheckResult` | `step_safety_validation()` | `step_postmortem()`, `print_summary()` |
| `version_validation` | `RemediationValidation` | `step_remediation_validation()` | `step_postmortem()`, `print_summary()` |

**Critical**: Never rename these keys — downstream stages use `.get()` chains on exact key names.

---

> **See also**: [Data Models](data-models.md) for dataclass field definitions | [Project Context](./../_bmad-output/project-context.md) for implementation rules
