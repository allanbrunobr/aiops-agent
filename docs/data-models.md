# AIOps Agent — Data Models

> All data models use Python `@dataclass`. No ORM, no Pydantic, no database — all in-memory for the prototype.

---

## Hub Data Contracts (Cross-Module)

> Includes both `@dataclass` and regular classes. Changing fields is a **breaking change**.
> See also: [API Contracts](api-contracts-internal.md) for interface signatures and pipeline stage keys.

### `data/sample_incident.py`

| Dataclass | Fields | Used By |
|-----------|--------|---------|
| **LogEntry** | `timestamp`, `level`, `service`, `message` | pipeline/log_parser, pipeline/telemetry_sanitizer, main |
| **MetricPoint** | `timestamp`, `service`, `metric_name`, `value` | pipeline/metric_filter, main |
| **Span** | `trace_id`, `span_id`, `parent_span_id`, `service`, `operation`, `start_time`, `duration_ms`, `status`, `attributes` | pipeline/trace_filter, main |
| **SimulatedIncident** | `logs: list[LogEntry]`, `metrics: list[MetricPoint]`, `traces: list[Span]`, `incident_id`, `title`, `severity`, `start_time` | main, pipeline/telemetry_sanitizer |

### `safety/tnr.py`

| Dataclass | Fields | Used By |
|-----------|--------|---------|
| **HealthSnapshot** | `timestamp`, `error_rate`, `latency_p99_ms`, `availability`, `connection_pool_active`, `connection_pool_max` + `health_score` @property | main, agents/mitigator |
| **MitigationAction** | `description`, `command`, `risk_level`, `rollback_command` | main, agents/mitigator, safety/pre_validator |
| **TNRReport** | `result`, `pre_snapshot`, `post_snapshot`, `action`, `health_delta`, `message` | main |

### `pipeline/aggregator.py`

| Class | Type | Fields/Methods | Used By |
|-------|------|----------------|---------|
| **AggregatedReport** | class (not dataclass) | `log_templates`, `metric_anomalies`, `slow_traces`, `total_logs`, `total_metrics`, `total_traces` + 11 methods including `to_text()`, `to_compact_text()`, `affected_services` @property, `severity` @property | agents/diagnoser, pipeline/context_compressor, main |

### `rag/indexer.py`

| Dataclass | Fields | Used By |
|-----------|--------|---------|
| **IndexedChunk** | `text`, `source_type`, `metadata` | rag/retriever, main |

---

## Sanitizer Dataclasses

| File | Dataclass | Fields |
|------|-----------|--------|
| `pipeline/telemetry_sanitizer.py` | **SanitizationResult** | `original`, `sanitized`, `taint_level: TaintLevel`, `detections: list[str]`, `fields_sanitized` |
| `pipeline/telemetry_sanitizer.py` | **SanitizationReport** | `total_entries`, `clean_entries`, `suspicious_entries`, `tainted_entries`, `adversarial_entries`, `fields_sanitized`, `adversarial_payloads_blocked: list[str]` + `threat_detected` @property, `sanitization_rate` @property |

---

## Pipeline Processor Dataclasses

Co-located with their processor class, one per file.

| File | Dataclass | Fields |
|------|-----------|--------|
| `pipeline/log_parser.py` | **LogTemplate** | `cluster_id`, `template`, `count`, `services: set`, `levels: set`, `sample_messages: list`, `relevance_score` |
| `pipeline/metric_filter.py` | **MetricAnomaly** | `service`, `metric_name`, `current_value`, `mean`, `std_dev`, `sigma`, `shape`, `description` |
| `pipeline/trace_filter.py` | **SlowCallPath** | `call_path: list[str]`, `avg_duration_ms`, `count`, `slowest_span_service`, `slowest_span_op` |
| `pipeline/context_compressor.py` | **CompressedChunk** | `source_type`, `priority`, `lines: list[str]`, `summary` |
| `pipeline/context_compressor.py` | **CompressionStats** | `original_tokens`, `compressed_tokens`, `ratio`, `chunks_removed`, `lines_deduplicated` |

---

## Safety Dataclasses

| File | Dataclass | Fields |
|------|-----------|--------|
| `safety/pre_validator.py` | **SafetyCheckResult** | `is_safe`, `risk_level`, `approval_status`, `checks_passed`, `checks_failed`, `warnings`, `blast_radius`, `risk_score`, `recommendation`, `requires_human` |
| `safety/pre_validator.py` | **BlastRadius** | `affected_services`, `affected_resources`, `is_production`, `has_database_impact`, `has_network_impact` |
| `safety/version_validator.py` | **RemediationValidation** | `is_safe`, `risk: RemediationRisk`, `checks: list[VersionCheck]`, `blocked_patterns`, `warnings`, `recommendation` |
| `safety/version_validator.py` | **VersionCheck** | `component`, `current_version`, `target_version`, `is_safe`, `known_cves: list` |

---

## Enums

| File | Enum | Values |
|------|------|--------|
| `safety/tnr.py` | **TNRResult** | SUCCESS, REGRESSION, ROLLBACK_COMPLETE, ERROR |
| `safety/pre_validator.py` | **RiskLevel** | LOW, MEDIUM, HIGH, CRITICAL |
| `safety/pre_validator.py` | **ApprovalStatus** | AUTO_APPROVED, PENDING_APPROVAL, APPROVED, REJECTED, BLOCKED |
| `pipeline/telemetry_sanitizer.py` | **TaintLevel** | CLEAN, LOW, MEDIUM, HIGH, CRITICAL |
| `pipeline/context_compressor.py` | **Priority** | CRITICAL, HIGH, MEDIUM, LOW |
| `safety/version_validator.py` | **RemediationRisk** | SAFE, CAUTION, DANGEROUS, MALICIOUS |
| `agents/orchestrator.py` | **State** | IDLE, DETECTING, DIAGNOSING, MITIGATING, VERIFYING, RESOLVED |

---

## Data Flow

```
SimulatedIncident (LogEntry, MetricPoint, Span)
    │
    ▼ step_sanitize()
SanitizationReport (mutates incident in-place)
    │
    ▼ step_pipeline()
AggregatedReport (LogTemplate, MetricAnomaly, SlowCallPath)
    │
    ├─▶ step_compression() → CompressedChunk[], CompressionStats
    │                                    │
    ├─▶ step_rag() → IndexedChunk[] ─┐  │
    │                                 ▼  ▼
    └──────────────────────▶ step_agents()
                             (report + retriever)
result dict with:
  ├── detection: {is_incident, severity, signals}
  ├── diagnosis: {root_cause, evidence, affected_services, confidence, timeline}
  ├── mitigation: {action: MitigationAction, ...}
  ├── tnr: TNRReport
  │
  ▼ step_safety_validation()
SafetyCheckResult
  │
  ▼ step_remediation_validation()
RemediationValidation
  │
  ▼ step_postmortem()
Path (output/postmortem_YYYY-MM-DD_HHMMSS.md)
```

---

> **See also**: [API Contracts](api-contracts-internal.md) | [Project Context](./../_bmad-output/project-context.md)
