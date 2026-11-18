"""Correção pós-revisão #4: CoTObservabilityRetriever reentrante.

Antes ``search_history``/``current_iteration`` eram estado de INSTÂNCIA num
retriever montado uma vez em ``create_app`` (``app/builders.py``). O diagnoser
chama ``reset_context()`` por diagnose (``agents/diagnoser.py:121``); runs
A2A/MCP concorrentes (``asyncio.to_thread(engine.run, ...)`` em
``interfaces/http/routes/a2a.py:289``) compartilham a mesma instância e
contaminam o histórico um do outro — a mesma classe de bug que F6-3 corrigiu
no BlueprintEngine.

O estado de expansão agora é por-run (thread-local), isolando runs concorrentes
sem mudar a API pública (reset_context/search_with_cot/get_search_summary).
"""

from __future__ import annotations

import threading

import pytest

from rag.cot_retriever import CoTObservabilityRetriever


class _NoIndexIndexer:
    """Indexer sem FAISS/model — search() retorna [] graceful (sem embeddings)."""

    index = None
    model = None


@pytest.mark.unit
def test_concurrent_runs_do_not_share_cot_history():
    retriever = CoTObservabilityRetriever(_NoIndexIndexer())
    n = 6
    # Barrier por iteração força os dois runs a intercalar search_with_cot.
    barrier = threading.Barrier(2, timeout=10)
    captured: dict[str, dict] = {}

    def worker(tag: str) -> None:
        retriever.reset_context()
        barrier.wait()  # ambos os runs ativos ao mesmo tempo
        for i in range(n):
            retriever.search_with_cot(f"{tag}-svc query-{i}", top_k=3)
            barrier.wait()  # intercala cada iteração entre os threads
        captured[tag] = {
            "queries": [c.query for c in retriever.search_history],
            "iteration": retriever.current_iteration,
            "summary": retriever.get_search_summary(),
        }

    t1 = threading.Thread(target=worker, args=("alpha",))
    t2 = threading.Thread(target=worker, args=("beta",))
    t1.start()
    t2.start()
    t1.join(15)
    t2.join(15)

    for tag in ("alpha", "beta"):
        got = captured[tag]
        # Antes do fix: histórico misturado/zerado pelo reset_context do outro run.
        assert got["queries"] == [f"{tag}-svc query-{i}" for i in range(n)]
        assert got["iteration"] == n
        assert f"{tag}-svc" in got["summary"]
        other = "beta" if tag == "alpha" else "alpha"
        assert other not in got["summary"]  # sem contaminação cruzada


@pytest.mark.unit
def test_reset_context_isolated_per_thread():
    """reset_context() num thread não apaga o histórico de outro thread ativo."""
    retriever = CoTObservabilityRetriever(_NoIndexIndexer())
    started = threading.Barrier(2, timeout=10)
    done = threading.Barrier(2, timeout=10)
    seen: dict[str, int] = {}

    def long_run() -> None:
        retriever.reset_context()
        retriever.search_with_cot("payment-svc timeout", top_k=3)
        retriever.search_with_cot("payment-svc latency", top_k=3)
        started.wait()  # histórico de 'long' tem 2 entradas
        done.wait()  # espera o outro thread resetar + buscar
        seen["long"] = len(retriever.search_history)

    def short_run() -> None:
        started.wait()
        retriever.reset_context()  # não deve tocar o histórico de 'long'
        retriever.search_with_cot("auth-service error", top_k=3)
        seen["short"] = len(retriever.search_history)
        done.wait()

    t1 = threading.Thread(target=long_run)
    t2 = threading.Thread(target=short_run)
    t1.start()
    t2.start()
    t1.join(15)
    t2.join(15)

    assert seen["long"] == 2  # antes: zerado pelo reset_context do short_run
    assert seen["short"] == 1


@pytest.mark.unit
def test_single_thread_behavior_unchanged():
    """API sequencial preservada: acumula, reseta, sumariza."""
    retriever = CoTObservabilityRetriever(_NoIndexIndexer())
    retriever.search_with_cot("payment-svc timeout", top_k=3)
    retriever.search_with_cot("auth-service error", top_k=3)
    assert retriever.current_iteration == 2
    assert len(retriever.search_history) == 2
    assert "Iteration 1" in retriever.get_search_summary()
    retriever.reset_context()
    assert retriever.current_iteration == 0
    assert retriever.search_history == []
    assert retriever.get_search_summary() == "No searches performed yet."
    retriever.search_with_cot("new investigation", top_k=3)
    assert retriever.current_iteration == 1
    assert retriever.search_history[0].query_expansion is None
