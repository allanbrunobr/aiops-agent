"""Execuções concorrentes da mesma engine não misturam os resultados dos nós.

`app.state.engine` é uma só instância, e A2A e MCP a rodam em threads
(`to_thread`). `run()` zerava `self.results` e anexava nele: uma execução
iniciada no meio de outra apagava os nós da primeira, e o `pipeline.completed`
da primeira saía com `nodes_run` errado (pendência anotada na P2).
"""
from __future__ import annotations

import threading

import pytest
from structlog.testing import capture_logs

from blueprint.engine import BlueprintEngine
from blueprint.nodes import BlueprintNode, NodeType, OnFailure, PipelineDefinition
from tests.test_engine import _make_state

pytestmark = pytest.mark.unit


def _node(node_id, fn):
    return BlueprintNode(id=node_id, node_type=NodeType.DETERMINISTIC, execute=fn,
                         on_failure=OnFailure.ABORT)


def test_concurrent_run_keeps_its_own_node_results():
    engine = BlueprintEngine()
    a_inside = threading.Event()
    release_a = threading.Event()

    def wait_for_b(state):
        a_inside.set()
        assert release_a.wait(5)
        return state

    slow = PipelineDefinition(name="slow", nodes=[
        _node("a1", lambda s: s), _node("a2", wait_for_b), _node("a3", lambda s: s),
    ])
    # 2 nós, não 1: com 1, a lista compartilhada antiga trocava a1 por b1 e a
    # contagem da "slow" dava 3 por coincidência (o teste passava no defeito).
    fast = PipelineDefinition(name="fast", nodes=[
        _node("b1", lambda s: s), _node("b2", lambda s: s),
    ])

    with capture_logs() as logs:
        t = threading.Thread(target=engine.run, args=(slow, _make_state()))
        t.start()
        assert a_inside.wait(5)
        engine.run(fast, _make_state())  # roda inteira no meio da "slow"
        release_a.set()
        t.join(5)

    completed = {e["pipeline"]: e for e in logs if e["event"] == "pipeline.completed"}
    assert completed["fast"]["nodes_run"] == 2
    assert completed["slow"]["nodes_run"] == 3
