"""F6-5: extração tolerante de JSON das respostas de LLM (core/llm_json.py).

Antes, cinco call sites do AgentOrchestrator faziam json.loads(text) dentro de
`except Exception` genérico: uma resposta válida embrulhada em ```json ... ```
virava "parse failed" silencioso e caía no plano/reflexão default.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.llm_json import LLMJsonError, extract_json, extract_json_object


@pytest.mark.unit
def test_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json("[1, 2]") == [1, 2]


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        '```json\n{"decision": "continue"}\n```',
        '```\n{"decision": "continue"}\n```',
        'Here is the plan:\n```json\n{"decision": "continue"}\n```\nLet me know.',
    ],
)
def test_code_fenced_json(text):
    assert extract_json_object(text) == {"decision": "continue"}


@pytest.mark.unit
def test_json_embedded_in_prose():
    text = 'Sure! The result is {"root_cause": "pool", "nested": {"x": [1, 2]}} — done.'
    assert extract_json_object(text)["nested"] == {"x": [1, 2]}


@pytest.mark.unit
def test_braces_inside_strings_do_not_break_balancing():
    text = 'prefix {"msg": "a } inside \\" quoted", "ok": true} suffix'
    assert extract_json_object(text) == {"msg": 'a } inside " quoted', "ok": True}


@pytest.mark.unit
def test_non_json_raises_with_preview():
    with pytest.raises(LLMJsonError) as ei:
        extract_json("I cannot help with that.")
    assert "not JSON" in str(ei.value)
    assert ei.value.text == "I cannot help with that."


@pytest.mark.unit
def test_object_required():
    with pytest.raises(LLMJsonError):
        extract_json_object("[1, 2, 3]")


@pytest.mark.unit
def test_none_and_empty_raise():
    with pytest.raises(LLMJsonError):
        extract_json(None)  # type: ignore[arg-type]
    with pytest.raises(LLMJsonError):
        extract_json("   ")


# ── integração com o orchestrator ──────────────────────────────────────────


@pytest.mark.unit
def test_orchestrator_accepts_fenced_plan(tmp_path):
    """Plano em code-fence não cai mais no _default_plan."""
    from agents.orchestrator import AgentOrchestrator
    from rag.memory import IncidentMemoryDB

    plan = {
        "hypothesis": "pool exhaustion",
        "steps": [
            {
                "step_id": 1,
                "goal": "check pool",
                # precisa estar em agents.orchestrator._VALID_TOOLS, senão o
                # plano é descartado por outro motivo (tool inválida) e o teste
                # não provaria nada sobre o parse do code-fence.
                "tool": "query_logs",
                "tool_args": {"service": "payment-svc", "keyword": "pool"},
                "expected_signal": "exhausted",
            }
        ],
    }
    import json

    client = MagicMock()
    client.create_message.return_value = MagicMock(
        content=[MagicMock(text="```json\n" + json.dumps(plan) + "\n```")]
    )
    orch = AgentOrchestrator(
        client=client,
        model="t",
        memory_db=IncidentMemoryDB(db_path=str(tmp_path / "m.db")),
        skill_router=None,
    )
    report = MagicMock()
    report.to_compact_text.return_value = "report"
    report.log_templates = []
    report.affected_services = set()
    from agents.skill_router import DEFAULT_SKILL

    result = orch._generate_plan(report, DEFAULT_SKILL, [])
    assert result.hypothesis == "pool exhaustion"
    assert [s.tool for s in result.steps] == ["query_logs"]


@pytest.mark.unit
def test_orchestrator_reuses_single_diagnoser(tmp_path):
    from agents.orchestrator import AgentOrchestrator
    from rag.memory import IncidentMemoryDB

    orch = AgentOrchestrator(
        client=None,
        model="t",
        memory_db=IncidentMemoryDB(db_path=str(tmp_path / "m.db")),
        skill_router=None,
    )
    assert orch._diagnoser is orch._diagnoser
    assert orch._diagnoser.reporter is orch.reporter


@pytest.mark.unit
def test_skill_evolver_model_follows_config_and_env(monkeypatch):
    from agents.skill_evolver import SkillEvolver

    monkeypatch.delenv("AIOPS_LLM_MODEL", raising=False)
    from config.loader import LLMConfig

    assert SkillEvolver(client=None, memory_db=None).model == LLMConfig().model_name
    monkeypatch.setenv("AIOPS_LLM_MODEL", "env-model")
    assert SkillEvolver(client=None, memory_db=None).model == "env-model"
    assert (
        SkillEvolver(client=None, memory_db=None, model="explicit").model == "explicit"
    )


@pytest.mark.unit
def test_trace_filter_python_path_is_causal_not_temporal():
    """F6-5: Python agora usa o DFS parent→child como o Rust."""
    from datetime import datetime, timedelta

    import pipeline.trace_filter as tf
    from data.models import Span

    now = datetime.now()

    def span(tid, sid, parent, svc, start_ms, dur):
        return Span(
            trace_id=tid,
            span_id=sid,
            parent_span_id=parent,
            service=svc,
            operation=f"op-{svc}",
            start_time=now + timedelta(milliseconds=start_ms),
            duration_ms=dur,
            status="ok",
            attributes={},
        )

    spans = [
        # filho começa ANTES do pai no relógio
        span("t", "c", "b", "postgres", 50, 900.0),
        span("t", "a", None, "gateway", 0, 1000.0),
        span("t", "b", "a", "payment", 20, 950.0),
        span("fast", "x", None, "gateway", 0, 10.0),
    ]
    f = tf.TraceFilter(percentile_threshold=50.0, max_hops=3)
    out = f._filter_python(spans)
    slow = next(p for p in out if p.trace_id == "t")
    assert slow.path == ["gateway", "payment", "postgres"]
    assert slow.operations == ["op-gateway", "op-payment", "op-postgres"]


@pytest.mark.unit
def test_trace_filter_python_no_nan_when_all_durations_equal():
    import math
    from datetime import datetime

    import pipeline.trace_filter as tf
    from data.models import Span

    now = datetime.now()
    spans = [
        Span(
            trace_id=f"t{i}",
            span_id="r",
            parent_span_id=None,
            service="s",
            operation="o",
            start_time=now,
            duration_ms=100.0,
            status="ok",
            attributes={},
        )
        for i in range(5)
    ]
    out = tf.TraceFilter()._filter_python(spans)
    assert out, (
        "todos iguais → todos >= p95 → todos retornados (dedup por path deixa 1)"
    )
    assert not math.isnan(out[0].normal_duration_ms)
    assert out[0].normal_duration_ms == 100.0
