"""CoT retriever chama o reranker com a assinatura certa e uma vez só (F5-12).

P3-9 (docs/analise-20261109.md): search_with_cot fazia
`self.reranker.rerank(query, raw)`, mas a assinatura é
`rerank(results, query, top_k=...)`; e o `search()` herdado já aplicava o
mesmo reranker internamente — o rerank acontecia duas vezes, uma delas com
os argumentos trocados.
"""
from __future__ import annotations

import pytest

from rag.cot_retriever import CoTObservabilityRetriever

pytestmark = pytest.mark.unit


class _Recorder:
    def __init__(self):
        self.calls = []

    def rerank(self, results, query, top_k=10, use_tier2=False):
        self.calls.append({"results": results, "query": query, "top_k": top_k})
        return list(results)


class _CoT(CoTObservabilityRetriever):
    def __init__(self, reranker):
        super().__init__(indexer=object(), reranker=reranker)
        self.search_calls = []

    def search(self, query, top_k=10, source_filter=None, use_reranking=True):
        self.search_calls.append({"query": query, "use_reranking": use_reranking})
        if use_reranking and self.reranker is not None:
            return self.reranker.rerank(["r1", "r2"], query, top_k=top_k)
        return ["r1", "r2"]


def test_reranker_receives_results_then_query_once():
    reranker = _Recorder()
    retriever = _CoT(reranker)

    results = retriever.search_with_cot("payment-svc timeout", top_k=2)

    assert len(reranker.calls) == 1, "rerank deve acontecer uma única vez"
    call = reranker.calls[0]
    assert call["results"] == ["r1", "r2"]
    assert call["query"] == "payment-svc timeout"  # query original, não a expandida
    assert call["top_k"] == 2
    assert results == ["r1", "r2"]
    assert retriever.search_calls[0]["use_reranking"] is False
