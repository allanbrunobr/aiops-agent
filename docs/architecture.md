# AIOps Agent — Architecture Summary

> Concise architecture reference for AI agents and developers. For the full academic design document (78KB, 14 sections), see [`ARCHITECTURE - AIOPS-AGENT.md`](../ARCHITECTURE%20-%20AIOPS-AGENT.md).

---

## Architecture Type

**Sequential pipeline + multi-agent state machine** — a single-process CLI application that runs observability data through a fixed sequence of processing stages, with AI-driven decision-making in the agent layer.

Not a web service, not a microservice, not event-driven. All execution is synchronous and single-threaded.

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

Transitions enforced by `VALID_TRANSITIONS` dict in `orchestrator.py`. Invalid transitions raise `InvalidTransitionError`. History is append-only for timeline generation.

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
