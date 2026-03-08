# AIOps Agent — Development Guide

---

## Prerequisites

- **Python 3.12** (required — uses `from __future__ import annotations` + `X | None` syntax)
- **venv** at `.venv/`
- **ANTHROPIC_API_KEY** in `.env` file (optional — falls back to `--no-llm` mode)

---

## Setup

```bash
# Create and activate virtual environment
python3.12 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure API key (optional)
echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
```

### Dependency Notes

- `torch` (~2GB) and `sentence-transformers` are the heaviest installs
- `numpy < 2.0.0` is pinned due to faiss-cpu/torch compatibility
- `drain3 >= 0.9.11` returns `dict` (not object) — older versions break `LogParser`
- `sentence-transformers` pulls ~10 transitive dependencies (scipy, scikit-learn, Pillow, etc.)

---

## Running

```bash
# Full pipeline (requires ANTHROPIC_API_KEY)
python main.py

# Heuristic-only mode (no API key needed)
python main.py --no-llm

# Skip FAISS/embedding model load (faster startup, ~5-10s saved)
python main.py --no-rag

# Compact terminal output
python main.py --compact

# Combine flags
python main.py --no-llm --no-rag --compact
```

All three modes (`default`, `--no-llm`, `--no-rag`) must always work. Missing API key auto-falls back to `--no-llm`.

### Standalone Module Demos

```bash
# Adversarial telemetry attack demo
python -m pipeline.telemetry_sanitizer

# Version validation demo (safe + unsafe scenarios)
python -m safety.version_validator
```

---

## Testing

**No test framework is currently configured.** The project has zero tests.

When adding tests:
- Use `pytest` as the framework
- Mock `DiagnosisAgent._get_client()` to avoid real Claude API calls
- Mock heavy ML dependencies (`torch`, `sentence-transformers`, `faiss`) in unit tests
- `data/sample_incident.py` uses `random.seed(42)` — output is deterministic and testable
- `TNRSafetyGuard.simulate_check()` is the test-friendly TNR method (no real execution)
- Use `--no-llm` mode for integration testing (exercises full pipeline without API keys)
- The `demo_*()` functions in `telemetry_sanitizer.py` and `version_validator.py` serve as informal smoke tests

---

## Output

Postmortems are saved to `output/postmortem_YYYY-MM-DD_HHMMSS.md`. The `output/` directory is auto-created by `main.py`.

---

## Adding New Pipeline Stages

1. Create a `step_*()` function in `main.py` following existing pattern
2. Stage numbering: primary stages increment (6, 7...), sub-stages use letter suffix (4d, 4e...)
3. Write output to `result["stages"]["stage_name"]` dict
4. Update `step_postmortem()` to include the new stage in generated postmortems
5. Update `print_summary()` to display the new stage in terminal output
6. Stage numbers in function names and `Rule()` display headers must stay in sync

## Adding New Data Sources

1. Subclass the appropriate ABC in `tools/interfaces.py` (`MetricsSource`, `LogSource`, `TraceSource`, or `IncidentTracker`)
2. Implement all abstract methods
3. Wire it in `main.py` (replace the `Simulated*` source)
4. Dependency direction: `data/` → `pipeline/` → `rag/` → `agents/` → `safety/`

## Adding New Safety Validators

1. New validators should include a `demo_*()` function with both safe and unsafe scenarios
2. Include `if __name__ == "__main__"` for standalone testing
3. Never weaken existing blocklists (`BLOCKED_COMMANDS`, `BLOCKED_REPOSITORIES`, `INSECURE_CONFIG_PATTERNS`)

---

## Code Style

- `from __future__ import annotations` at top of every module
- `@dataclass` for all data models (not Pydantic)
- `X | None` syntax, never `Optional[X]`
- `field(default_factory=...)` for mutable defaults
- Docstrings and comments: Brazilian Portuguese (PT-BR)
- LLM prompts, CLI output, variable names: English
- Terminal output: always use `rich` (Console, Panel, Table, Tree) — never bare `print()`
- Only `main.py` and `agents/*` create `console = Console()` at module level
- Files: `snake_case.py` | Classes: `PascalCase` | Methods: `snake_case` | Constants: `UPPER_SNAKE_CASE`

---

> **See also**: [Source Tree](source-tree-analysis.md) | [Data Models](data-models.md) | [API Contracts](api-contracts-internal.md) | [Project Context](../_bmad-output/project-context.md)
