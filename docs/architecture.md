# AIOps Agent — Architecture Summary

> Concise architecture reference for what this repository actually runs.

---

## Architecture Type

One process, two ways in.

The default `python main.py` path is the sequential demo in `main.py`. It does not use `app/builders.py`. `--data-source databricks` changes the data source only. The Blueprint graph (`blueprint/`) runs when you pass `--pipeline-v1`, and when the HTTP app is created with `create_app`.

`create_app` mounts health, optional MCP, optional OAuth, and A2A. The webhook receiver is a separate process (`infra/run_webhook_server.py`). Its pipeline executor still logs `pipeline.stub_executed`. The demo dashboard is `interfaces/demo/server.py` and binds to `127.0.0.1` unless you pass `--host`.

The agent path does not execute `kubectl`. The TNR check on that path compares fixed before/after health snapshots. `--no-llm` diagnosis is a keyword heuristic.

---

## Executive Summary

AIOps Agent is a prototype for **agentic incident response** using LLM + RAG + a multi-agent state machine. It processes simulated observability data (logs, metrics, traces) through:

1. **Telemetry sanitization** — blocks adversarial payloads before LLM processing
2. **Pre-processing** — Drain3 log clustering, 3-sigma anomaly detection, p95 trace filtering
3. **Context compression** — priority-based token budget optimization
4. **RAG indexing** — FAISS + sentence-transformers semantic search
5. **Multi-agent pipeline** — Detect → Diagnose (ReAct) → Mitigate → Verify
6. **Safety verification** — 4-layer: Sanitizer → PreValidator → TNR → VersionValidator
7. **Postmortem generation** — structured markdown output

Based on 7 academic papers (2024-2025): STRATUS, OpsAgent, RCACopilot, Acon, AOI, TAMO, and "When AIOps Become AI Oops".

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        main.py (orchestrator)                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────┐    ┌──────────────┐    ┌────────────────────┐     │
│  │  data/    │───▶│  pipeline/   │───▶│  rag/              │     │
│  │ sample_   │    │ sanitizer    │    │ indexer + retriever │     │
│  │ incident  │    │ aggregator   │    └─────────┬──────────┘     │
│  └──────────┘    │ compressor   │              │                 │
│                   └──────┬───────┘              │                 │
│                          │                      │                 │
│                          ▼                      ▼                 │
│                   ┌──────────────────────────────┐               │
│                   │         agents/               │               │
│                   │  orchestrator (state machine)  │               │
│                   │  detector → diagnoser → mitigator │           │
│                   └──────────────┬────────────────┘               │
│                                  │                                │
│                                  ▼                                │
│                   ┌──────────────────────────────┐               │
│                   │         safety/               │               │
│                   │  pre_validator → tnr →        │               │
│                   │  version_validator            │               │
│                   └──────────────┬────────────────┘               │
│                                  │                                │
│                                  ▼                                │
│                         output/postmortem_*.md                    │
└─────────────────────────────────────────────────────────────────┘
```

---

## Technology Stack

| Category | Technology | Purpose |
|----------|-----------|---------|
| Language | Python 3.12 | Core runtime |
| LLM | Anthropic Claude (claude-sonnet-4-20250514) | ReAct diagnosis |
| Log Processing | Drain3 | Template extraction via log clustering |
| Embeddings | sentence-transformers (all-MiniLM-L6-v2) | 384-dim embeddings |
| Vector Search | FAISS (IndexFlatIP) | Cosine similarity on normalized embeddings |
| Terminal UI | Rich | Panels, tables, trees, color-coded output |
| Numerical | NumPy (< 2.0) | Metric statistics |
| ML Runtime | PyTorch (2.1-2.4) | sentence-transformers backend |
| Config | python-dotenv | API key from .env |

---

## Key Design Decisions

1. **All in-memory, no persistence** — prototype uses `@dataclass`, no ORM or database
2. **Simulated data only** — `random.seed(42)` ensures deterministic, reproducible scenarios
3. **LLM fallback to heuristics** — every LLM path has a `_heuristic_*()` fallback (never crashes)
4. **Human-in-the-loop** — MitigationAgent proposes but never executes
5. **4-layer safety** — defense-in-depth: sanitize input → validate action → test regression → check versions
6. **MCP-ready interfaces** — ABCs in `tools/interfaces.py` designed for future MCP server integration
7. **Lazy ML loading** — sentence-transformers and FAISS loaded only when needed (`--no-rag` skips them)

---

## State Machine (STRATUS)

```
IDLE → DETECTING → DIAGNOSING → MITIGATING → VERIFYING → RESOLVED
                       ▲                          │
                       └──────── ROLLBACK ─────────┘
                       (on TNR regression detection)
```

The CLI records these names in a list inside `main.py` (`_transition`). There is no `VALID_TRANSITIONS` map and no `InvalidTransitionError` in this tree. A TNR pass appends `RESOLVED` before the safety gate. If that gate sets `requires_human`, the process exits 3 and the summary says `AWAITING HUMAN`.

---

## Safety Architecture (Quad Layer)

| Layer | Component | Paper | Purpose |
|-------|-----------|-------|---------|
| 1 | `TelemetrySanitizer` | AIOpsShield | Blocks adversarial payloads in input telemetry |
| 2 | `PreExecutionValidator` | STRATUS | Blast radius estimation + command blocklists |
| 3 | `TNRSafetyGuard` | STRATUS | Transactional No-Regression health comparison |
| 4 | `VersionValidator` | AIOpsShield | CVE lookup + config pattern validation |

**Critical**: Never skip a layer. Never weaken blocklists. Safety rules are non-negotiable.

---

> **See also**: [Full Architecture Document](../ARCHITECTURE%20-%20AIOPS-AGENT.md) | [Source Tree](source-tree-analysis.md) | [Data Models](data-models.md) | [API Contracts](api-contracts-internal.md) | [Development Guide](development-guide.md)
