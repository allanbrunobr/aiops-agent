# AIOps Agent — Project Overview

---

## What Is This?

AIOps Agent is a **prototype for agentic incident response** — an AI system that can detect, diagnose, and propose mitigations for infrastructure incidents using observability data (logs, metrics, traces).

It demonstrates the full incident response pipeline in a single CLI application, combining LLM reasoning (Claude), RAG-based similarity search (FAISS), and a multi-agent state machine with safety guardrails.

---

## Quick Facts

| | |
|---|---|
| **Type** | Standalone CLI (Python 3.12) |
| **Architecture** | Sequential pipeline + multi-agent state machine |
| **LLM** | Anthropic Claude (claude-sonnet-4-20250514) |
| **Data** | Simulated (deterministic with seed 42) |
| **Papers** | 7 academic papers (2024-2025) |
| **Files** | 15 source files, ~4,900 lines |
| **Tests** | None (prototype) |
| **Dependencies** | 9 (anthropic, drain3, sentence-transformers, faiss-cpu, torch, numpy, rich, python-dotenv, transformers) |

---

## The Incident Scenario

The prototype simulates a **payment-svc connection pool exhaustion** caused by a bad config deploy:

- **500 log entries** across 6 services (payment-svc, order-svc, api-gateway, etc.)
- **240 metric data points** (error rates, latency, connection pools)
- **100 distributed traces** with varying latencies

The agent processes this data and produces a diagnosis, mitigation proposal, and postmortem — all in one run.

---

## Academic Foundations

| Paper | Year | Contribution to Prototype |
|-------|------|--------------------------|
| STRATUS (NeurIPS) | 2025 | State machine + TNR safety mechanism |
| OpsAgent (Nankai) | 2025 | Multi-modal observability processing |
| RCACopilot (Microsoft) | 2024 | ReAct-based root cause analysis |
| Acon (Microsoft) | 2025 | Context compression for LLM |
| AOI (Columbia) | 2025 | Priority-based token budgeting |
| TAMO (Shandong) | 2025 | Tool-assisted metric observation |
| When AIOps Become AI Oops | 2025 | Adversarial defense (AIOpsShield) |

---

## Repository Structure

```
aiops-agent/
├── main.py              # Entry point & pipeline orchestrator
├── agents/              # Multi-agent decision layer
├── pipeline/            # Data pre-processing
├── rag/                 # FAISS semantic search
├── safety/              # 4-layer safety mechanisms
├── tools/               # ABC interfaces (MCP-ready)
├── data/                # Simulated incident data
├── output/              # Generated postmortems
└── docs/                # Project documentation
```

---

## How to Run

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env  # optional
python main.py                              # full demo
python main.py --no-llm                     # heuristic mode (no API key)
```

---

> **See also**: [Architecture](architecture.md) | [Development Guide](development-guide.md) | [Source Tree](source-tree-analysis.md)
