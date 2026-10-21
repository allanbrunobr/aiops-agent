# AIOps Agent

**English** · [Português](README.md)

[![CI](https://github.com/allanbrunobr/aiops-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/allanbrunobr/aiops-agent/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12%2B-blue)
![Rust](https://img.shields.io/badge/rust-PyO3%20(optional)-orange)
![Status](https://img.shields.io/badge/status-proof--of--concept-yellow)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

An **autonomous incident response** platform that combines an LLM, RAG and a multi-agent state machine to diagnose incidents and propose mitigations from observability data (logs, metrics and traces).

The pipeline covers the full incident lifecycle: telemetry sanitization, preprocessing, context compression, semantic search over past incidents, ReAct diagnosis, mitigation proposal, safety verification and postmortem generation.

The implementation is based on seven academic AIOps papers (2024–2025). See [Academic references](#academic-references).

![Demo dashboard during a connection pool exhaustion incident](docs/screenshots/demo-dashboard.png)

> Most code comments, the narrative demo mode, the ADRs and the `infra/` guides are written in Portuguese. The other documents under `docs/` are in English.

---

## Contents

- [Key features](#key-features)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [CLI usage](#cli-usage)
- [Demo dashboard](#demo-dashboard)
- [Configuration](#configuration)
- [Integrations](#integrations)
- [Rust extension (optional)](#rust-extension-optional)
- [Tests and CI](#tests-and-ci)
- [Local Docker environment](#local-docker-environment)
- [Repository layout](#repository-layout)
- [Academic references](#academic-references)
- [Known limitations](#known-limitations)
- [Further documentation](#further-documentation)
- [License](#license)

---

## Key features

- **State machine orchestration** (`IDLE → DETECTING → DIAGNOSING → MITIGATING → VERIFYING → RESOLVED`) with rollback.
- **ReAct diagnosis with Claude**: the agent iteratively queries logs, metrics, traces and the knowledge base.
- **Heuristic mode** (`--no-llm`): runs the full pipeline with no LLM calls and no cost.
- **Telemetry preprocessing**: Drain3 for logs, 3-sigma detection for metrics and p95 filtering for traces.
- **Priority-based context compression** within a token budget.
- **RAG** over past incidents with FAISS and `sentence-transformers` (`all-MiniLM-L6-v2`, 384 dimensions).
- **Safety layers**: sanitization against adversarial payloads, *blast radius* calculation, version/CVE checks and *Transactional No-Regression* (TNR).
- **Predictive detection**: risk score and degradation ETA before the incident happens.
- **Interfaces**: CLI, real-time web dashboard (WebSocket), MCP server (stdio/SSE), A2A API and webhook normalizers (Grafana, PagerDuty, Databricks).
- **Progressive Slack notifications** (Block Kit) and optional Jira issue creation.
- **Optional Rust acceleration** (PyO3) for the performance-critical stages, with automatic fallback to Python.

---

## Architecture

### Pipeline flow

```
 Alert / observability data (simulated or Databricks)
        │
        ▼
 TelemetrySanitizer ─── blocks adversarial payloads (reward hacking)
        │
        ▼
 Aggregator ─────────── Drain3 (logs) · 3-sigma (metrics) · p95 (traces)
        │
        ▼
 ContextCompressor ──── priority-based compression within the token budget
        │
        ▼
 RAG Retriever ──────── FAISS + sentence-transformers (384d)
        │
        ▼
 Orchestrator (state machine)
   IDLE → DETECTING → DIAGNOSING → MITIGATING → VERIFYING → RESOLVED
        ├─ Diagnoser ──────── ReAct loop with Claude (logs, metrics, traces, search)
        ├─ PreValidator ───── blast radius and risk score
        ├─ VersionValidator ─ version and CVE checks
        └─ TNR ────────────── rollback if service health gets worse
        │
        ▼
 Postmortem (Markdown/JSON) · Slack · Jira
```

The project has two execution engines:

| Engine | How to enable | Description |
|--------|---------------|-------------|
| V0 (sequential) | default | Sequential pipeline in `main.py` |
| V1 (Blueprint Engine) | `--pipeline-v1` or `--data-source databricks` | Orchestration as a typed graph (`blueprint/`) |

### Layers

The code follows a layered (ports and adapters) architecture. Decisions are recorded in [`docs/adr/`](docs/adr/).

| Layer | Directory | Responsibility |
|-------|-----------|----------------|
| Domain | `domain/` | Immutable entities (`IncidentState`, `NormalizedAlert`, `MitigationAction`) with no external dependencies |
| Application | `application/ports/` | Ports (`LLMClient`, `IncidentStore`, `CredentialProvider`, `Reporter`) |
| Adapters | `adapters/` | Port implementations (Anthropic, in-memory storage, env credentials, reporters) |
| Interfaces | `interfaces/` | Entry points: HTTP, MCP and demo |
| Composition | `app/builders.py` | Composition root: builds and wires the dependencies |

---

## Quick start

### Prerequisites

- Python 3.12 or later
- (Optional) `ANTHROPIC_API_KEY` for LLM mode
- (Optional) Stable Rust toolchain to build the native extension

### Installation

```bash
git clone https://github.com/allanbrunobr/aiops-agent.git
cd aiops-agent

python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` includes PyTorch, FAISS and `sentence-transformers`, so the first install downloads a few hundred MB.

### Minimal configuration

```bash
cp .env.example .env.local
# Edit .env.local and set ANTHROPIC_API_KEY (only needed in LLM mode)
```

### First run

```bash
# Heuristic mode: no LLM calls, no cost
python main.py --no-llm

# Full pipeline with Claude
python main.py
```

The default scenario (`payment-pool-exhaustion`) simulates connection pool exhaustion in `payment-svc` after a faulty deploy. The result is rendered in the terminal with `rich`, and the postmortem is written to `output/`.

- On the first run, the embedding model (`all-MiniLM-L6-v2`) is downloaded from Hugging Face. Use `--no-rag` to skip this step or to run offline.
- In the default scenario, the CLI ends with **exit code `3`**. This is expected: the proposed mitigation has medium risk and waits for human approval. See [Exit codes](#cli-usage).

---

## CLI usage

```bash
python main.py [options]
```

| Option | Description | Default |
|--------|-------------|---------|
| `--no-llm` | Heuristic mode, no calls to Claude | off |
| `--no-rag` | Do not build the RAG index (faster startup) | off |
| `--rebuild-rag` | Rebuild the RAG index, ignoring the cache | off |
| `--compact` | Compact terminal output | off |
| `--scenario NAME` | `payment-pool-exhaustion`, `memory-leak` or `unknown-fault` | `payment-pool-exhaustion` |
| `--data-source SOURCE` | `simulated` or `databricks` | `simulated` |
| `--pipeline-v1` | Use the Blueprint Engine (V1) | off |
| `--config FILE` | YAML file with analysis thresholds | `config/defaults.yaml` |
| `--output-dir DIR` | Postmortem output directory | `output/` |
| `--output-format FORMAT` | `markdown`, `json` or `both` | `markdown` |
| `--tracker TRACKER` | Create an issue after diagnosis (`jira` or `none`) | no tracker |

> `--data-source databricks` cannot be combined with `--scenario`.

**Exit codes**

| Code | Meaning |
|------|---------|
| `0` | Incident resolved and verified by TNR |
| `1` | Unrecoverable error in a pipeline stage |
| `2` | Regression detected by TNR (post-action health worse than pre-action) |
| `3` | Action blocked by the safety layer (requires human approval) |

---

## Demo dashboard

A web dashboard that simulates incidents in real time. The backend (FastAPI + WebSocket) and the static frontend are served by the same process.

```bash
source .venv/bin/activate
python interfaces/demo/server.py            # default port: 47891
python interfaces/demo/server.py --port 8080
```

Open **http://localhost:47891/demo/**.

> Use the virtualenv interpreter (`.venv/bin/python`). With the system Python, dependencies such as `structlog` may be missing. In that case the log shows `SlackNotifier init falhou` and the demo pipeline may be incomplete.

### Scenarios

| Scenario | Simulated duration | Behavior |
|----------|--------------------|----------|
| Payment Pool Exhaustion | 32 min | `connection_pool_active` rises from 5% to 86% along a logistic curve |
| Pool Exhaustion + Recovery | 40 min | Pool exhaustion; at minute 25 the Ops team raises `max_connections` and the system recovers |
| JVM Memory Leak | 60 min | `memory_used_mb` grows linearly from 2048 MB to 7987 MB |
| Unknown Fault Mode | 40 min | Unseen log templates appear at minute 5; latency spikes at minute 10 |

Each tick of logs, metrics and traces is streamed over WebSocket. When the risk score crosses the threshold, the agent runs the diagnosis and proposes a fix.

### Narrative mode (`/demo/story`)

A page for presentations. It uses the same WebSocket and overlays a Portuguese narrative on each phase (`PLAYING → DEGRADING → WARNING → ALERT → INCIDENT → RESOLVED`), with a simulated Slack message, projected financial impact and an animated timeline.

```
http://localhost:47891/demo/story?scenario=payment-pool-exhaustion
http://localhost:47891/demo/story?scenario=payment-pool-recovery
```

Only these two scenarios have a narrative. The others show only the technical panels. The narrative content is available at `GET /demo/api/narrative/{scenario}`.

### Native window (macOS, optional)

```bash
.venv/bin/pip install pywebview
.venv/bin/python interfaces/demo/desktop_app.py
```

The launcher starts the server in a subprocess, waits for `/demo/` to respond and opens a WebKit window. The server stops when the window is closed.

### Optional demo features

- **LLM diagnosis**: set `ANTHROPIC_API_KEY` in `.env.local`. Without the key, the demo uses heuristics.
- **Slack notifications**: set `SLACK_BOT_TOKEN` and `SLACK_CHANNEL`. The demo publishes the `prediction.warning`, `prediction.critical`, `diagnosis.completed` and `incident.resolved` events.

---

## Configuration

### Environment variables

The CLI and the demo load `.env.local` automatically. The CLI also reads `.env`. There are two templates: [`.env.example`](.env.example), with the CLI variables (Anthropic and Databricks), and [`env.example`](env.example), with the variables for the local environment in `infra/` (Grafana webhook and Slack).

| Variable | Purpose | Required |
|----------|---------|----------|
| `ANTHROPIC_API_KEY` | Diagnosis with Claude | Only in LLM mode |
| `AIOPS_LLM_MODEL` | Claude model (default: `claude-sonnet-4-20250514`) | No |
| `SLACK_BOT_TOKEN` / `SLACK_CHANNEL` | Slack notifications | No |
| `SLACK_API_BASE_URL` | Alternative Slack API URL (for example, the mock in `infra/`) | No |
| `GRAFANA_WEBHOOK_TOKEN` | Grafana webhook authentication | No |
| `DATABRICKS_HOST` / `DATABRICKS_TOKEN` / `DATABRICKS_WAREHOUSE_ID` | `--data-source databricks` | Only with Databricks |
| `JIRA_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` / `JIRA_PROJECT_KEY` | `--tracker jira` | Only with Jira |

> Never commit `.env*` files with real credentials. `.gitignore` already excludes them.

### Configuration files

- [`config/defaults.yaml`](config/defaults.yaml): analysis thresholds, Blueprint Engine, webhook, Slack and MCP settings. Values can be overridden by variables with the `AIOPS__` prefix (for example, `AIOPS__MCP__OAUTH__JWT_SECRET`).
- [`config.yaml`](config.yaml): per-service criticality map, used for the *blast radius* calculation.

---

## Integrations

| Integration | Location | Notes |
|-------------|----------|-------|
| Slack | `interfaces/http/notifications/slack.py` | Progressive Block Kit, exponential retry and local fallback in `output/`. Guide: [`infra/SLACK-SETUP.md`](infra/SLACK-SETUP.md) |
| Jira | `tools/jira_tracker.py` | Enabled with `--tracker jira` |
| Databricks | `tools/databricks_connector.py` | Real data source via `--data-source databricks` |
| MCP | `interfaces/mcp/` | Tools `diagnose_incident`, `validate_action` and `search_incidents`. stdio and SSE transports, with rate limiting and OAuth 2.1 |
| A2A | `interfaces/http/routes/a2a.py` | Agent-to-agent API mounted by the HTTP app |
| Webhooks | `interfaces/http/webhook/` | Normalizers for Grafana, PagerDuty and Databricks, with deduplication |

MCP server over stdio (Claude Desktop and local use):

```bash
python -m interfaces.mcp.server
```

---

## Rust extension (optional)

The performance-critical stages (`log_parser`, `metric_filter`, `trace_filter`, `context_compressor`, `telemetry_sanitizer`, `cusum_ewma`, `trace_predictor`) have Rust implementations exposed through PyO3 as the `_aiops_rust` module. Without the extension, the pipeline falls back to the Python implementations automatically.

```bash
scripts/build_rust.sh            # development build into .venv
scripts/build_rust.sh --release  # optimized build (required for benchmarks)
cargo test --manifest-path rust/Cargo.toml
```

---

## Tests and CI

```bash
python -m pytest tests/                                # full suite
python -m pytest tests/ -m unit                        # unit tests (no ML, no API)
python -m pytest tests/ -m "integration or snapshot"   # integration and snapshot tests
```

Available markers: `unit`, `integration`, `snapshot` and `slow`, defined in `pyproject.toml`.

The tests in `tests/rust/` require the [Rust extension](#rust-extension-optional) to be built. Without it, run `python -m pytest tests/ --ignore=tests/rust`. The performance benchmark only passes with the `--release` build.

The [`.github/workflows/ci.yml`](.github/workflows/ci.yml) workflow runs three jobs in parallel, with no API keys and no model downloads:

1. **Unit Tests**: minimal dependencies.
2. **Integration & Snapshot Tests**: full dependencies.
3. **Rust Build & PyO3 Extension**: `cargo test`, extension build and full suite.

To reproduce CI locally:

```bash
scripts/ci-local.sh              # all jobs
scripts/ci-local.sh unit         # a single job: unit | integration | rust
```

---

## Local Docker environment

The [`infra/`](infra/) directory starts an end-to-end environment for testing alert intake: an exporter simulates `payment-svc` metrics, Prometheus scrapes them, Grafana evaluates the alert rules and sends a webhook to the AIOps Agent. A Slack API mock is included.

```bash
docker compose -f infra/docker-compose.yaml up -d --build   # Prometheus, Grafana, exporter and Slack mock
GRAFANA_WEBHOOK_TOKEN=test-grafana-token-local python infra/run_webhook_server.py   # port 47890
bash infra/test-webhook.sh                                  # sends a simulated Grafana alert
```

| Service | Address |
|---------|---------|
| Grafana | http://localhost:3001 (`admin` / `aiops123`) |
| Prometheus | http://localhost:9090 |
| Metrics exporter | http://localhost:9101/metrics |
| Slack mock | http://localhost:47892/_mock/messages |
| Webhook server | http://localhost:47890/health |

To send notifications to the mock instead of the real Slack, set `SLACK_API_BASE_URL=http://localhost:47892/api/` and `SLACK_BOT_TOKEN=xoxb-mock-token`. The full walkthrough (in Portuguese) is in [`infra/README.md`](infra/README.md).

---

## Repository layout

```
aiops-agent/
├── main.py              # CLI and pipeline orchestration
├── agents/              # Orchestrator, Diagnoser, Detector, Mitigator, skill router/evolver, A2A client
├── pipeline/            # Sanitizer, parsers, filters, aggregator, compressor, predictors and risk scoring
├── rag/                 # FAISS indexing, retrievers, reranker, query expansion and memory
├── safety/              # PreValidator, TNR, VersionValidator and auditing
├── blueprint/           # Blueprint Engine (pipeline as a typed graph)
├── domain/              # Immutable domain entities
├── application/ports/   # Application-layer ports
├── adapters/            # Port implementations (LLM, persistence, credentials, reporters)
├── app/                 # Composition root
├── interfaces/
│   ├── demo/            # Web dashboard (FastAPI + WebSocket) and narrative mode
│   ├── http/            # FastAPI app: health, webhooks, notifications, A2A, admin
│   └── mcp/             # MCP server (stdio/SSE) and rate limiter
├── auth/                # OAuth 2.1, PKCE, JWT/OIDC validation
├── core/                # Logging, lifecycle and shared protocols
├── config/              # YAML configuration and loader (pydantic-settings)
├── data/                # Simulated scenarios, models, knowledge base and golden sets
├── tools/               # Connectors (Databricks, Jira, Slack, FAISS)
├── validation/          # Validation runner and scenarios (RAG, knowledge)
├── rust/                # Native extension `_aiops_rust` (PyO3)
├── infra/               # Docker environment (Prometheus, Grafana) for local testing
├── scripts/             # Rust build, local CI and scenario generator
├── tests/               # Unit, integration and snapshot tests
└── docs/                # Architecture, ADRs, guides and reference papers
```

To create a new incident scenario, use `scripts/new_scenario.py` and the template in [`docs/contributing/scenario-template.md`](docs/contributing/scenario-template.md).

---

## Academic references

| Technique | Paper | Where it is used |
|-----------|-------|------------------|
| State machine orchestration with rollback | STRATUS (NeurIPS 2025) | `agents/orchestrator.py`, `safety/tnr.py` |
| Iterative ReAct diagnosis with tools | OpsAgent (2025) | `agents/diagnoser.py` |
| Multi-signal correlation (logs, metrics, traces) | RCACopilot (Microsoft, 2024) | `pipeline/aggregator.py` |
| Priority-based context compression | Acon (2025) | `pipeline/context_compressor.py` |
| Token budget allocation for observability | AOI (2025) | `pipeline/context_compressor.py` |
| Tool-assisted RAG for RCA | TAMO (2025) | `rag/` |
| Defense against adversarial telemetry (AIOpsShield) | When AIOps Become "AI Oops" (2025) | `pipeline/telemetry_sanitizer.py`, `safety/` |

The PDFs are in [`docs/papers/`](docs/papers/).

---

## Known limitations

- **The webhook-triggered pipeline is still a stub.** The `infra/run_webhook_server.py` server receives, normalizes, deduplicates and queues alerts, but the `PipelineRunner` only logs `pipeline.stub_executed`. The full diagnosis runs through the CLI (`main.py`), the demo and the MCP and A2A interfaces.
- This is a **proof of concept**. Mitigation actions are proposed and validated, but they are not executed against real infrastructure.

---

## Further documentation

| Document | Contents | Language |
|----------|----------|----------|
| [`docs/index.md`](docs/index.md) | Documentation index | English |
| [`docs/architecture.md`](docs/architecture.md) | Architecture decisions, state machine and safety layers | English |
| [`docs/development-guide.md`](docs/development-guide.md) | Development environment and conventions | English |
| [`docs/adr/`](docs/adr/) | Architecture Decision Records | Portuguese |
| [`infra/README.md`](infra/README.md) | Local Docker environment (Grafana, Prometheus, Slack mock) | Portuguese |
| [`infra/SLACK-SETUP.md`](infra/SLACK-SETUP.md) | Setting up a real Slack App | Portuguese |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Contribution guide | English |

---

## License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for details.
