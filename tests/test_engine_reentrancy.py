"""F6-3: BlueprintEngine reentrante — results por run, não por instância.

Antes ``self.results`` era resetado no início de ``run()``: duas execuções
concorrentes na MESMA instância (A2A/MCP compartilham o engine de
``app.state``) misturavam seus NodeResults.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from blueprint.engine import BlueprintEngine
from blueprint.nodes import BlueprintNode, NodeType, OnFailure, PipelineDefinition
from config.loader import AppConfig
from domain.incident_state import IncidentState


def _state(iid: str) -> IncidentState:
    now = datetime.now(timezone.utc)
    return IncidentState(
        incident_id=iid,
        correlation_id=iid,
        alert=None,
        received_at=now,
        started_at=now,
        no_llm=True,
    )


def _node(nid: str, fn) -> BlueprintNode:
    return BlueprintNode(
        id=nid, node_type=NodeType.DETERMINISTIC, execute=fn, on_failure=OnFailure.ABORT
    )


@pytest.mark.unit
def test_node_results_are_attached_to_the_returned_state():
    engine = BlueprintEngine(config=AppConfig())
    pipeline = PipelineDefinition(
        name="t", nodes=[_node("a", lambda s: s), _node("b", lambda s: s)]
    )
    out = engine.run(pipeline, _state("inc-1"))
    ids = [r.node_id for r in out.metadata["_node_results"]]
    assert ids == ["a", "b"]
    # compat: self.results reflete o último run
    assert [r.node_id for r in engine.results] == ["a", "b"]


@pytest.mark.unit
def test_concurrent_runs_on_same_engine_do_not_share_results():
    engine = BlueprintEngine(config=AppConfig())
    barrier = threading.Barrier(2, timeout=5)

    def sync_then_pass(state):
        barrier.wait()  # garante que os dois runs estão dentro do loop ao mesmo tempo
        return state

    def mk(n_nodes: int) -> PipelineDefinition:
        return PipelineDefinition(
            name=f"p{n_nodes}",
            nodes=[_node("sync", sync_then_pass)]
            + [_node(f"n{i}", lambda s: s) for i in range(n_nodes - 1)],
        )

    outs: dict[str, IncidentState] = {}

    def worker(iid: str, n: int):
        outs[iid] = engine.run(mk(n), _state(iid))

    t1 = threading.Thread(target=worker, args=("inc-A", 2))
    t2 = threading.Thread(target=worker, args=("inc-B", 5))
    t1.start()
    t2.start()
    t1.join(5)
    t2.join(5)

    a = outs["inc-A"].metadata["_node_results"]
    b = outs["inc-B"].metadata["_node_results"]
    assert len(a) == 2 and len(b) == 5  # antes: listas misturadas/zeradas
    assert all(r.success for r in a + b)


@pytest.mark.unit
def test_results_available_after_abort_for_compat():
    def boom(state):
        raise RuntimeError("fail")

    from blueprint.engine import PipelineAborted

    engine = BlueprintEngine(config=AppConfig())
    with pytest.raises(PipelineAborted):
        engine.run(
            PipelineDefinition(
                name="t", nodes=[_node("ok", lambda s: s), _node("bad", boom)]
            ),
            _state("i"),
        )
    assert [r.node_id for r in engine.results] == ["ok", "bad"]
    assert engine.results[-1].success is False
