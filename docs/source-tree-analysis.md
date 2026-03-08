# AIOps Agent — Source Tree Analysis

> Annotated directory structure with purpose, entry points, and critical paths.

---

## Project Root

```
aiops-agent/
├── main.py                            # ENTRY POINT — CLI pipeline orchestrator (731 lines)
│                                      #   Usage: python main.py [--no-llm] [--no-rag]
│                                      #   Contains all step_*() functions and print_summary()
├── requirements.txt                   # 9 dependencies with version constraints
├── CLAUDE.md                          # Claude Code AI agent guidance
├── ARCHITECTURE - AIOPS-AGENT.md      # 78KB academic architecture document (14 sections)
│
├── agents/                            # Layer 3: Multi-agent decision-making
│   ├── __init__.py                    #   (empty)
│   ├── orchestrator.py                #   State machine: IDLE→DETECTING→DIAGNOSING→MITIGATING→VERIFYING→RESOLVED
│   │                                  #   Hub: mutable state (context dict + history list)
│   ├── diagnoser.py                   #   HIGHEST COMPLEXITY (12) — ReAct loop with Claude API
│   │                                  #   7 tool functions, LLM + heuristic fallback paths
│   │                                  #   Model: claude-sonnet-4-20250514 (hardcoded)
│   ├── detector.py                    #   Lightweight: anomaly threshold detection (67 lines, stdlib-only)
│   └── mitigator.py                   #   Proposes MitigationAction from REMEDIATION_PATTERNS
│                                      #   Lateral dependency: mitigator → safety/tnr.py
│
├── pipeline/                          # Layer 2: Data pre-processing
│   ├── __init__.py                    #   (empty)
│   ├── aggregator.py                  #   FAN-IN HUB: combines log_parser + metric_filter + trace_filter
│   │                                  #   Exports AggregatedReport (11 methods, central data contract)
│   ├── log_parser.py                  #   Drain3 log template clustering (drain3 dependency)
│   ├── metric_filter.py              #   3-sigma anomaly detection (stdlib-only)
│   ├── trace_filter.py                #   p95 slow trace filtering (stdlib-only)
│   ├── context_compressor.py          #   Acon+AOI token budget compression (stdlib-only, 474 lines)
│   └── telemetry_sanitizer.py         #   HIGH COMPLEXITY (10) — AIOpsShield adversarial defense
│                                      #   Mutates incident in-place; _abstraction_map persists per run
│
├── rag/                               # Layer 2.5: Retrieval-Augmented Generation
│   ├── __init__.py                    #   (empty)
│   ├── indexer.py                     #   FAISS IndexFlatIP + sentence-transformers (lazy-loaded)
│   │                                  #   Model: all-MiniLM-L6-v2 (384 dimensions)
│   └── retriever.py                   #   Semantic search over IndexedChunk[]
│
├── safety/                            # Layer 4: Safety mechanisms (4-layer architecture)
│   ├── __init__.py                    #   (empty)
│   ├── tnr.py                         #   HUB: exports HealthSnapshot, MitigationAction, TNRReport
│   │                                  #   Transactional No-Regression (STRATUS paper)
│   │                                  #   regression_threshold = -5.0 (deliberate design)
│   ├── pre_validator.py               #   Pre-execution blast radius + command safety (stdlib-only)
│   │                                  #   BLOCKED_COMMANDS, BLOCKED_REPOSITORIES blocklists
│   └── version_validator.py           #   CVE lookup + config pattern validation (stdlib-only)
│                                      #   Includes demo_version_validation() smoke test
│
├── tools/                             # Layer 1: Extensibility interfaces
│   ├── __init__.py                    #   (empty)
│   └── interfaces.py                  #   4 ABCs (MCP-ready) + 3 simulated implementations
│                                      #   No IncidentTracker simulation exists yet
│
├── data/                              # Layer 0: Simulated data generation
│   ├── __init__.py                    #   (empty)
│   └── sample_incident.py            #   HUB: LogEntry, MetricPoint, Span, SimulatedIncident
│                                      #   random.seed(42) — deterministic output
│                                      #   generate_logs(500), generate_metrics(60min), generate_traces(100)
│
├── output/                            # Generated artifacts (auto-created)
│   └── postmortem_YYYY-MM-DD_HHMMSS.md
│
└── docs/                              # Project documentation
    ├── data-models.md                 # Dataclass catalog and data flow diagram
    ├── api-contracts-internal.md      # Internal API contracts and ReAct tools
    ├── project-scan-report.json       # Documentation workflow state
    ├── API-CONTRACTS-OBSERVABILITY.md # Future observability platform contracts
    ├── PLATFORM-INTEGRATION-ARCHITECTURE.md
    ├── SECURITY-COMPLIANCE-GUIDE.md
    ├── SRE-INTEGRATION-GUIDE.md
    ├── APRESENTACAO-SRE-AIOPS-AGENT.html/pdf
    └── papers/                        # 7 reference papers (STRATUS, Acon, AOI, etc.)
```

---

## Critical Directories

| Directory | Role | Hub Files | Stdlib-Only Modules |
|-----------|------|-----------|---------------------|
| `data/` | Simulated incident generation | `sample_incident.py` | — |
| `pipeline/` | Telemetry processing & aggregation | `aggregator.py` | metric_filter, trace_filter, context_compressor |
| `agents/` | AI-driven decision agents | — | orchestrator, detector |
| `rag/` | Semantic search over telemetry | `indexer.py` | — |
| `safety/` | 4-layer safety architecture | `tnr.py` | pre_validator, version_validator |
| `tools/` | ABC interfaces for future MCP | — | — |

---

## Dependency Flow (Layer Direction)

```
Layer 0: data/          → provides raw dataclasses
Layer 1: tools/         → provides ABC interfaces
Layer 2: pipeline/      → processes data into AggregatedReport
Layer 2.5: rag/         → indexes report for semantic search
Layer 3: agents/        → consumes report + retriever for diagnosis
Layer 4: safety/        → validates agent-proposed actions
         main.py        → orchestrates all layers sequentially
```

All imports flow downward (lower layer → consumed by higher layer). The sole lateral exception is `agents/mitigator.py → safety/tnr.py`.

---

## Entry Points

| Entry Point | Command | Purpose |
|-------------|---------|---------|
| `main.py` | `python main.py` | Full pipeline with LLM |
| `main.py` | `python main.py --no-llm` | Heuristic mode (no API key needed) |
| `main.py` | `python main.py --no-rag` | Skip FAISS/embedding model load |
| `telemetry_sanitizer.py` | `python -m pipeline.telemetry_sanitizer` | Standalone adversarial demo |
| `version_validator.py` | `python -m safety.version_validator` | Standalone version validation demo |
