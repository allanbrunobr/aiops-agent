# Adding a New Diagnostic Scenario

This guide walks you through adding a new diagnostic scenario to the AIOps Agent.
A developer familiar with the codebase should be able to complete this in **< 2 hours**.

## Quick-Start Checklist

- [ ] Plan scenario (root cause, affected services, timeline)
- [ ] Create `data/{name}_scenario.py` — simulated telemetry
- [ ] Create `data/golden/{name}_expected.json` — expected output
- [ ] Create `data/knowledge/{name}.json` — RAG knowledge pattern
- [ ] Validate with `python main.py --no-llm`
- [ ] Add tests

Or use the scaffold script to generate boilerplate:
```bash
python scripts/new_scenario.py {scenario-name}
```

---

## Phase 1: Plan Your Scenario

Define:
- **Root cause**: What goes wrong? (e.g., "Redis connection timeout due to maxmemory reached")
- **Affected services**: Which services are impacted?
- **Timeline**: Pre-incident → Trigger → Degradation → Full incident → (Optional: Fix)
- **Detection signals**: What the detector should find (≥2 correlated signals)

The detector requires **at least 2 correlated signals** to declare an incident:
- Error logs (≥ `error_threshold` errors)
- Metric anomalies (3-sigma deviation)
- Slow traces (above p95 latency)

---

## Phase 2: Generate Telemetry Data

Create `data/{name}_scenario.py`:

```python
from __future__ import annotations

"""
Cenário simulado: {SCENARIO_DESCRIPTION}.

Causa raiz: {ROOT_CAUSE_DESCRIPTION}.
"""

import random
import datetime

from data.models import LogEntry, MetricPoint, Span, SimulatedIncident


def generate_{name}(seed: int = 42) -> SimulatedIncident:
    """Gera cenário de {scenario_description}.

    Args:
        seed: Semente para reprodutibilidade. Mesmo seed -> mesma saída.
    """
    random.seed(seed)

    incident_start = datetime.datetime(2026, 3, 4, 14, 19, 0)

    logs = _generate_logs(incident_start)
    metrics = _generate_metrics(incident_start)
    traces = _generate_traces(incident_start)

    return SimulatedIncident(
        logs=logs,
        metrics=metrics,
        traces=traces,
    )
```

**Rules:**
- Always use `random.seed(seed)` — determinism is required for golden-file testing
- Generate ≥50 log entries, ≥100 metric points, ≥50 traces
- Include a "normal" baseline period before the incident

---

## Phase 3: Define Expected Output (Golden File)

Create `data/golden/{name}.json`:

```json
{
  "scenario_name": "{name}",
  "scenario_seed": 42,
  "expected_detection": {
    "incident_declared": true,
    "correlated_signals": {
      "error_logs": true,
      "metric_anomalies": true,
      "slow_traces": true
    },
    "min_signals_required": 2
  },
  "expected_diagnosis": {
    "affected_services": ["{primary-service}"],
    "root_cause_contains": "{keyword-in-root-cause}",
    "severity": "high"
  },
  "expected_affected_services": ["{primary-service}"],
  "validation_mode": "no-llm"
}
```

The `root_cause_contains` field is a **substring match** (case-insensitive) against
the heuristic diagnosis output in `--no-llm` mode.

---

## Phase 4: Create Knowledge Pattern

Create `data/knowledge/{name}.json`:

```json
[
  {
    "pattern_id": "pat-{name}-001",
    "service": "{primary-service}",
    "symptoms": [
      "Observable symptom 1",
      "Observable symptom 2",
      "Observable symptom 3"
    ],
    "root_cause": "Exact root cause description",
    "resolution": "Step-by-step resolution",
    "tags": ["{tag1}", "{tag2}"],
    "confidence": 0.9,
    "is_negative_example": false,
    "created_at": "2026-03-08T00:00:00+00:00"
  }
]
```

This JSON is loaded by the knowledge base on startup and indexed by FAISS
for RAG retrieval during diagnosis.

---

## Phase 5: Validate

Run the pipeline in `--no-llm` mode:

```bash
python main.py --no-llm
```

Use the golden-file validator to verify output:

```python
from tests.support.golden_validator import validate_against_golden

result = validate_against_golden(
    actual_output={
        "incident_declared": True,
        "affected_services": ["payment-svc"],
        "root_cause": "connection pool exhausted",
    },
    golden_path="data/golden/{name}.json",
)
print(result.summary())
```

---

## Phase 6: Add Tests

Add tests in `tests/test_{name}.py`:

```python
def test_{name}_generates_incident():
    incident = generate_{name}(seed=42)
    assert incident is not None

def test_{name}_is_deterministic():
    inc1 = generate_{name}(seed=42)
    inc2 = generate_{name}(seed=42)
    assert len(inc1.logs) == len(inc2.logs)
```

---

## Worked Example: payment-pool-exhaustion

### Scenario

**Root cause:** A config deploy reduced `max_connections` from 200 to 50.
Payment service exhausted the connection pool within minutes.

**Affected services:** `payment-svc`

**Detection signals:**
- Error logs: 150+ connection timeout errors
- Metric anomaly: `connection_pool_active` at 100% (50/50)
- Slow traces: p95 latency > 5000ms on `/payment` endpoints

### Files Created

| File | Purpose |
|------|---------|
| `data/sample_incident.py` | Generates the simulated telemetry |
| `data/golden/payment-pool-exhaustion.json` | Expected output golden file |
| `data/knowledge/payment-pool-exhaustion.json` | RAG knowledge pattern |

### Running

```bash
python main.py --no-llm
```

Expected output (heuristic mode):
```
Root cause: Connection pool exhaustion (payment-svc)
Affected services: payment-svc
Confidence: 0.7
```

---

## File Structure Reference

```
data/
├── {name}_scenario.py     ← Simulated telemetry
├── golden/
│   └── {name}.json        ← Expected output (golden file)
└── knowledge/
    └── {name}.json        ← RAG knowledge pattern

tests/
└── test_{name}.py         ← Scenario tests
```

## Dependency Direction

`data/` modules only import from `data/models.py`. They do NOT import from:
- `agents/`, `pipeline/`, `safety/`, `rag/`, `server/`, `core/`

This keeps scenarios isolated and testable without the full pipeline.
