# AIOps Agent — Documentation Index

---

## Project Overview

- **Type:** Monolith (standalone CLI)
- **Language:** Python 3.12
- **Architecture:** Sequential pipeline + multi-agent state machine
- **Entry Point:** `main.py`
- **Tech Stack:** Anthropic Claude, Drain3, FAISS, sentence-transformers, Rich

---

## Generated Documentation

### Core

- [Project Overview](./project-overview.md) — What this project is, quick facts, academic foundations
- [Architecture](./architecture.md) — Architecture summary, design decisions, state machine, safety layers
- [Source Tree Analysis](./source-tree-analysis.md) — Annotated directory structure with layer mapping

### Development

- [Development Guide](./development-guide.md) — Setup, running, testing, code style, adding new stages

### Technical Reference

- [Data Models](./data-models.md) — All 20 dataclasses, 7 enums, data flow diagram
- [API Contracts (Internal)](./api-contracts-internal.md) — ABC interfaces, ReAct tools, pipeline stage keys

---

## Existing Documentation

- [Architecture Decision Records](./adr/) — ADRs for domain contracts, LLM client boundary and domain purity
- [Local Testing Setup](./LOCAL_TESTING_SETUP.md) — Local environment for end-to-end testing

### Reference Papers

- `docs/papers/STRATUS_NeurIPS_2025.pdf` — State machine + TNR safety
- `docs/papers/OpsAgent_Nankai_2025.pdf` — Multi-modal observability
- `docs/papers/Microsoft_RCACopilot_2024.pdf` — ReAct root cause analysis
- `docs/papers/Acon_Context_Compression_2025.pdf` — Context compression
- `docs/papers/AOI_Context_Compression_2025.pdf` — Priority token budgeting
- `docs/papers/TAMO_Tool_Assisted_RCA_2025.pdf` — Tool-assisted metrics
- `docs/papers/WhenAIOpsBecome AI Oops.pdf` — Adversarial defense

---

## Getting Started

```bash
# 1. Setup
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. (Optional) Configure API key
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env

# 3. Run
python main.py              # Full demo with LLM
python main.py --no-llm     # Heuristic mode (no API key needed)
python main.py --no-rag     # Skip FAISS model loading
```

---

*Generated: 2026-03-05 | Scan level: deep*
