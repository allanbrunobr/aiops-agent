"""
Testes para Epic 9: Guided Investigation — Chain-of-Thought Diagnosis.

Cobre:
  Story 9.1 — Search Context Tracking per Diagnosis Session
  Story 9.2 — Query Expansion and Follow-Up Suggestions
  Story 9.3 — Investigation Trail Integration with DiagnosisAgent
"""

from __future__ import annotations

import pytest

from rag.cot_retriever import CoTObservabilityRetriever, SearchContext
from rag.query_expansion import QueryExpander


# ── Fake helpers (sem FAISS / ML) ─────────────────────────────────────────────

class _FakeIndexer:
    """Indexer fake sem FAISS — search() retorna [] imediatamente."""
    index = None
    model = None


class _FakeChunk:
    """Chunk fake para SearchResult."""
    def __init__(self, text: str, source: str = "log"):
        self.text = text
        self.source = source
        self.metadata = {}


class _FakeSearchResult:
    """SearchResult fake sem dependência FAISS."""
    def __init__(self, text: str, score: float = 0.9):
        self.chunk = _FakeChunk(text)
        self.score = score


# ── Story 9.1: Search Context Tracking ────────────────────────────────────────

class TestResetContext:
    """AC 9.1.1 — reset_context limpa histórico e reinicia contador."""

    def test_reset_clears_search_history(self):
        r = CoTObservabilityRetriever(_FakeIndexer())
        r.search_history.append(SearchContext(1, "q", [], []))
        r.current_iteration = 1

        r.reset_context()

        assert r.search_history == []
        assert r.current_iteration == 0

    def test_reset_idempotent_on_empty(self):
        r = CoTObservabilityRetriever(_FakeIndexer())
        r.reset_context()
        assert r.search_history == []
        assert r.current_iteration == 0


class TestSearchWithCot:
    """AC 9.1.2 — search_with_cot anexa SearchContext ao histórico."""

    def test_appends_context_after_search(self):
        r = CoTObservabilityRetriever(_FakeIndexer())
        r.search_with_cot("payment-svc timeout")

        assert len(r.search_history) == 1
        ctx = r.search_history[0]
        assert ctx.iteration == 1
        assert ctx.query == "payment-svc timeout"
        assert isinstance(ctx.results, list)

    def test_increments_iteration_counter(self):
        r = CoTObservabilityRetriever(_FakeIndexer())
        r.search_with_cot("query one")
        r.search_with_cot("query two")
        r.search_with_cot("query three")

        assert len(r.search_history) == 3
        assert r.search_history[0].iteration == 1
        assert r.search_history[1].iteration == 2
        assert r.search_history[2].iteration == 3

    def test_extracts_service_entities_from_query(self):
        r = CoTObservabilityRetriever(_FakeIndexer())
        r.search_with_cot("payment-svc timeout error")

        ctx = r.search_history[0]
        assert "payment-svc" in ctx.entities

    def test_results_stored_in_context(self):
        r = CoTObservabilityRetriever(_FakeIndexer())
        results = r.search_with_cot("payment-svc timeout")

        ctx = r.search_history[0]
        assert ctx.results is results

    def test_no_reranker_falls_back_to_faiss(self):
        """AC 9.1.5 — sem reranker usa FAISS direto; contexto é rastreado."""
        r = CoTObservabilityRetriever(_FakeIndexer(), reranker=None)
        results = r.search_with_cot("some query")

        # FAISS não disponível → retorna [] — mas contexto é rastreado
        assert r.current_iteration == 1
        assert len(r.search_history) == 1
        assert results == []


class TestGetSearchSummary:
    """AC 9.1.3 — get_search_summary retorna string formatada de todas as iterações."""

    def test_summary_contains_all_iterations(self):
        r = CoTObservabilityRetriever(_FakeIndexer())
        r.search_with_cot("query one")
        r.search_with_cot("query two")
        r.search_with_cot("query three")

        summary = r.get_search_summary()

        assert "Iteration 1" in summary
        assert "Iteration 2" in summary
        assert "Iteration 3" in summary
        assert "query one" in summary
        assert "query two" in summary
        assert "query three" in summary

    def test_summary_includes_result_count(self):
        r = CoTObservabilityRetriever(_FakeIndexer())
        r.search_with_cot("payment-svc error")

        summary = r.get_search_summary()
        assert "results=0" in summary  # FAISS fake retorna []

    def test_summary_empty_on_no_searches(self):
        r = CoTObservabilityRetriever(_FakeIndexer())
        summary = r.get_search_summary()
        assert "No searches performed yet" in summary


class TestHistoryPersistenceWithoutReset:
    """AC 9.1.4 — sem reset entre incidentes, histórico persiste (design contract)."""

    def test_history_persists_without_reset(self):
        r = CoTObservabilityRetriever(_FakeIndexer())
        r.search_with_cot("first incident query")

        # Segundo incidente sem reset
        r.search_with_cot("second incident query")

        assert len(r.search_history) == 2
        assert r.search_history[0].query == "first incident query"


# ── Story 9.2: Query Expansion and Follow-Up Suggestions ──────────────────────

class TestQueryExpansion:
    """AC 9.2.1 — expand_query prefixa contexto de exclusão de entidades."""

    def test_expansion_prepends_exclusion_context(self):
        expander = QueryExpander()

        # Simula iteração 1 com entidades extraídas
        prev_ctx = SearchContext(
            iteration=1,
            query="payment-svc timeout",
            results=[],
            entities=["payment-svc", "metric:connection_pool"],
        )
        expanded = expander.expand_query("payment-svc timeout", [prev_ctx])

        assert "excluding already investigated" in expanded
        assert "payment-svc" in expanded
        assert "metric:connection_pool" in expanded

    def test_expansion_exact_format(self):
        expander = QueryExpander()

        prev_ctx = SearchContext(
            iteration=1,
            query="q",
            results=[],
            entities=["payment-svc", "metric:connection_pool"],
        )
        expanded = expander.expand_query("payment-svc timeout", [prev_ctx])

        assert expanded.startswith("payment-svc timeout")
        assert "(excluding already investigated: payment-svc, metric:connection_pool)" in expanded

    def test_no_expansion_on_empty_history(self):
        expander = QueryExpander()
        result = expander.expand_query("payment-svc timeout", [])
        assert result == "payment-svc timeout"

    def test_identical_query_still_gets_expanded(self):
        """AC 9.2.4 — query idêntica à anterior ainda recebe expansão."""
        expander = QueryExpander()

        prev_ctx = SearchContext(
            iteration=1,
            query="payment-svc timeout",
            results=[],
            entities=["payment-svc"],
        )
        expanded = expander.expand_query("payment-svc timeout", [prev_ctx])

        assert "excluding already investigated" in expanded


class TestSearchContextQueryExpansion:
    """AC 9.2.2 — SearchContext.query_expansion registra forma expandida."""

    def test_query_expansion_stored_in_context(self):
        r = CoTObservabilityRetriever(_FakeIndexer())
        # Primeira iteração cria entidades no histórico
        r.search_with_cot("payment-svc timeout")
        # Segunda iteração deve usar expansão
        r.search_with_cot("payment-svc error")

        ctx = r.search_history[1]
        assert ctx.query_expansion is not None
        assert "excluding already investigated" in ctx.query_expansion
        assert "payment-svc" in ctx.query_expansion

    def test_first_iteration_has_no_expansion(self):
        r = CoTObservabilityRetriever(_FakeIndexer())
        r.search_with_cot("payment-svc timeout")

        ctx = r.search_history[0]
        assert ctx.query_expansion is None  # Sem histórico anterior


class TestGenerateFollowUpQuery:
    """ACs 9.2.3 / 9.2.4 — _generate_follow_up_query retorna sugestão correta."""

    def test_connection_pool_pattern(self):
        expander = QueryExpander()
        ctx = SearchContext(
            iteration=1,
            query="q",
            results=[_FakeSearchResult("connection pool exhausted — max 10 connections reached")],
            entities=[],
        )
        suggestion = expander._generate_follow_up_query(ctx)
        assert suggestion == "connection pool configuration and limits"

    def test_memory_pattern(self):
        expander = QueryExpander()
        ctx = SearchContext(
            iteration=1,
            query="q",
            results=[_FakeSearchResult("heap memory usage at 98% — possible memory leak")],
            entities=[],
        )
        suggestion = expander._generate_follow_up_query(ctx)
        assert suggestion == "garbage collection and heap memory"

    def test_no_pattern_returns_none(self):
        """AC 9.2.6 — se nenhum padrão match, retorna None."""
        expander = QueryExpander()
        ctx = SearchContext(
            iteration=1,
            query="q",
            results=[_FakeSearchResult("disk I/O latency spike detected")],
            entities=[],
        )
        suggestion = expander._generate_follow_up_query(ctx)
        assert suggestion is None

    def test_no_results_returns_none(self):
        expander = QueryExpander()
        ctx = SearchContext(iteration=1, query="q", results=[], entities=[])
        suggestion = expander._generate_follow_up_query(ctx)
        assert suggestion is None

    def test_only_exclusion_when_no_follow_up(self):
        """AC 9.2.6 — sem follow-up, apenas exclusão é adicionada."""
        expander = QueryExpander()
        prev_ctx = SearchContext(
            iteration=1,
            query="q",
            results=[_FakeSearchResult("disk I/O spike")],  # Sem padrão conhecido
            entities=["payment-svc"],
        )
        expanded = expander.expand_query("new query", [prev_ctx])

        assert "(excluding already investigated: payment-svc)" in expanded
        assert "follow-up" not in expanded


# ── Story 9.3: Integration with DiagnosisAgent ────────────────────────────────

class _FakeRetriever:
    """ObservabilityRetriever fake para testes de DiagnosisAgent."""

    def __init__(self):
        self.search_calls: list[str] = []

    def search(self, query: str, top_k: int = 5):
        self.search_calls.append(query)
        return []

    def format_results(self, results) -> str:
        return "formatted results"


class _FakeCoTRetriever(CoTObservabilityRetriever):
    """CoTObservabilityRetriever fake para testes — sem FAISS."""

    def __init__(self):
        super().__init__(_FakeIndexer())
        self.reset_called = False

    def reset_context(self):
        self.reset_called = True
        super().reset_context()


class TestDiagnoseResetsContext:
    """AC 9.3.1 — diagnose() chama reset_context() no CoT retriever."""

    def test_reset_context_called_at_start(self):
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent(max_iterations=1)
        retriever = _FakeCoTRetriever()

        # Adiciona histórico para verificar reset
        retriever.search_history.append(SearchContext(1, "old query", [], []))
        retriever.current_iteration = 1

        # diagnose() sem LLM — vai usar heurística
        import io
        from contextlib import redirect_stdout, redirect_stderr
        import random
        from data.sample_incident import create_incident
        from pipeline.aggregator import Aggregator
        random.seed(42)
        incident = create_incident()
        aggregator = Aggregator()
        report = aggregator.process(incident.logs, incident.metrics, incident.traces)

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            agent.diagnose(report, retriever)

        assert retriever.reset_called is True


class TestExecuteToolSearchSimilarWithCoT:
    """ACs 9.3.2 / 9.3.3 / 9.3.4 — _execute_tool usa search_with_cot quando CoT disponível."""

    def _make_report(self):
        import random
        from data.sample_incident import create_incident
        from pipeline.aggregator import Aggregator
        random.seed(42)
        incident = create_incident()
        return Aggregator().process(incident.logs, incident.metrics, incident.traces)

    def test_uses_search_with_cot_for_cot_retriever(self):
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        retriever = _FakeCoTRetriever()
        report = self._make_report()

        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            result = agent._execute_tool("search_similar", {"query": "payment-svc timeout"}, report, retriever)

        # search_with_cot foi chamado
        assert retriever.current_iteration == 1

    def test_plain_retriever_uses_fallback_search(self):
        """AC 9.3.4 — retriever plain usa search() sem CoT."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        retriever = _FakeRetriever()
        report = self._make_report()

        result = agent._execute_tool("search_similar", {"query": "payment-svc"}, report, retriever)

        assert retriever.search_calls == ["payment-svc"]
        assert result == "formatted results"

    def test_cot_exception_returns_error_string(self):
        """AC 9.3.5 — exceção em search_with_cot retorna 'RAG search error — using baseline'."""
        from agents.diagnoser import DiagnosisAgent

        class _BrokenCoTRetriever(CoTObservabilityRetriever):
            def search_with_cot(self, *args, **kwargs):
                raise RuntimeError("simulated failure")

        agent = DiagnosisAgent()
        retriever = _BrokenCoTRetriever(_FakeIndexer())
        report = self._make_report()

        result = agent._execute_tool("search_similar", {"query": "q"}, report, retriever)

        assert result == "RAG search error — using baseline"

    def test_query_expansion_prepended_to_output(self):
        """AC 9.3.3 — query_expansion presente é prefixada na saída."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        retriever = _FakeCoTRetriever()
        report = self._make_report()

        # Primeira busca popula histórico com entidades
        import io
        from contextlib import redirect_stdout
        with redirect_stdout(io.StringIO()):
            agent._execute_tool("search_similar", {"query": "payment-svc timeout"}, report, retriever)
            # Segunda busca deve ter expansão
            result = agent._execute_tool("search_similar", {"query": "payment-svc error"}, report, retriever)

        # Expansão deve estar no output
        assert "[Query expanded to:" in result
