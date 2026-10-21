# Contributing to AIOps Agent

## Getting Started

### Prerequisites

- Python 3.12+
- Virtual environment setup:
  ```bash
  python3.12 -m venv .venv
  .venv/bin/pip install -r requirements.txt
  ```
- Copy `.env.example` to `.env` and add your `ANTHROPIC_API_KEY` (required for full LLM mode only)

### Project Structure

```
aiops-agent/
├── main.py                  # Entry point, CLI, pipeline orchestration
├── data/                    # Incident scenarios and data models
│   ├── models.py            # Dataclass definitions (LogEntry, MetricPoint, Span, etc.)
│   ├── registry.py          # Scenario registry (ScenarioRegistry)
│   ├── sample_incident.py   # Default scenario: connection pool exhaustion
│   ├── memory_leak_scenario.py  # Memory leak scenario
│   └── unknown_fault_scenario.py # Unknown fault handling
├── pipeline/                # Telemetry processing stages
│   ├── log_parser.py        # Drain3 log clustering
│   ├── metric_filter.py     # 3-sigma anomaly detection
│   ├── trace_filter.py      # p95 latency filtering
│   ├── aggregator.py        # Temporal aggregation
│   ├── context_compressor.py # Token-budgeted LLM context
│   └── telemetry_sanitizer.py # PII/adversarial payload detection
├── rag/                     # Retrieval-Augmented Generation
│   ├── indexer.py           # FAISS index builder
│   └── retriever.py         # Semantic search
├── agents/                  # Multi-agent system
│   ├── detector.py          # Incident detection
│   ├── diagnoser.py         # ReAct diagnosis (LLM + heuristic fallback)
│   ├── mitigator.py         # Remediation proposals
│   └── orchestrator.py      # STRATUS state machine
├── safety/                  # 4-layer safety architecture
│   ├── tnr.py               # Transactional No-Regression
│   ├── pre_validator.py     # Command blocklist validation
│   └── version_validator.py # CVE pattern detection
├── tools/                   # External integrations
│   └── interfaces.py        # ABC interfaces (MetricsSource, LogSource, etc.)
└── tests/                   # Test suite
```

## Development Workflow

1. **Fork** the repository (or create a feature branch)
2. **Branch** from `main` using the naming convention below
3. **Implement** your changes following code conventions
4. **Test** in all three run modes (see below)
5. **Submit** a PR with description of changes and test results

### Branching Convention

- `feature/<story-key>` — New features (e.g., `feature/4-1-databricks-connector`)
- `fix/<description>` — Bug fixes (e.g., `fix/metric-filter-nan-handling`)
- `docs/<description>` — Documentation changes (e.g., `docs/contributing-guide`)

## Three Run Modes

Contributors MUST verify their changes work in all three modes before submitting PRs:

### 1. Full Mode (default)
```bash
python main.py
```
Requires `ANTHROPIC_API_KEY` in `.env`. Uses LLM for diagnosis + RAG for semantic search.

### 2. No-LLM Mode
```bash
python main.py --no-llm
```
Heuristic-only diagnosis. No API calls. Still loads ML models for RAG.

### 3. No-LLM + No-RAG Mode
```bash
python main.py --no-llm --no-rag
```
Stdlib-only execution. No API calls, no ML model loading. Fastest mode for development iteration.

### Additional CLI Flags

- `--scenario <name>` — Select incident scenario (`connection-pool-exhaustion`, `memory-leak`, `unknown-fault`)
- `--output-dir <path>` — Custom postmortem output directory (default: `output/`)
- `--output-format <format>` — Postmortem output format: `markdown` (default), `json`, or `both`
- `--config <file>` — Path to TOML config file for analysis thresholds
- `--rebuild-rag` — Force RAG index reconstruction, ignoring any cached index
- `--compact` — Compact terminal output

### JSON Output Schema

When using `--output-format json` or `--output-format both`, the generated JSON file contains a stable schema with 10 top-level sections:

```json
{
  "meta": {
    "generated_at": "ISO 8601 timestamp",
    "scenario": "string",
    "llm_model": "string",
    "config_source": "string",
    "success": true,
    "duration_minutes": 0.0
  },
  "detection": { "is_incident": true, "severity": "string", "signals": [] },
  "diagnosis": { "root_cause": "string", "confidence": "string", "evidence": [], "timeline": "string" },
  "mitigation": { "action": { "description": "", "command": "", "risk_level": "", "rollback_command": "" }, "pattern_matched": "string" },
  "safety_validation": { "risk_level": "", "risk_score": 0, "approval_status": "", "requires_human": false },
  "remediation_validation": { "risk": "", "is_safe": true, "blocked_patterns": [], "warnings": [] },
  "verification": { "result": "", "health_delta": 0.0, "message": "", "pre_snapshot": {}, "post_snapshot": {} },
  "action_items": ["string"],
  "timeline": { "incident_timeline": { "deploy_time": "", "spike_start": "", "detection_time": "", "fix_time": "" }, "state_transitions": [] },
  "config": { "thresholds": {}, "source": "string" }
}
```

**Schema stability**: The 10 top-level keys are considered stable API. Sections may have empty values (`{}`, `[]`) when pipeline stages are skipped, but the keys are always present.

## Test Requirements

```bash
# Run all tests
pytest tests/ -v

# Run only unit tests
pytest tests/ -v -m unit

# Run only integration tests
pytest tests/ -v -m integration
```

- All tests MUST pass before submitting a PR
- Use `@pytest.mark.unit` for tests that don't call `step_*()` functions
- Use `@pytest.mark.integration` for tests that exercise pipeline stages
- Snapshot tests MUST remain deterministic (seed-based data generation)
- New features should include corresponding tests in `tests/test_<feature>.py`

## Code Conventions

### Python

- Every module starts with `from __future__ import annotations`
- Use `@dataclass` for all data models (not Pydantic, not plain dicts)
- Docstrings and comments in **PT-BR** (Portuguese)
- Variable names, CLI output, LLM prompts in **English**
- 7 modules are stdlib-only — do NOT add heavy imports to: `orchestrator`, `detector`, `metric_filter`, `trace_filter`, `context_compressor`, `pre_validator`, `version_validator`

### Safety Rules (Non-Negotiable)

- **Never** weaken `BLOCKED_COMMANDS` in `safety/pre_validator.py`
- **Never** remove or bypass safety layers (TNR, PreExecutionValidator, VersionValidator)
- **Never** log, print, or include API keys in any output
- **Never** skip `TelemetrySanitizer` before LLM API calls
- DiagnosisAgent tools must return **plain text strings**, not JSON objects
- `_heuristic_diagnosis()` must return the same dict shape as LLM diagnosis

