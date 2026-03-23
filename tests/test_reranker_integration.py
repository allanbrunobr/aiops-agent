"""
Testes de integração para Story 8.3: Reranker Integration, Config Wiring,
e Backward Compatibility.

Verifica que ObservabilityRetriever sem reranker mantém comportamento V1,
que o reranker é chamado corretamente quando configurado, e que falhas no
reranker resultam em fallback silencioso para resultados FAISS brutos.
"""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock, patch

import pytest

from rag.retriever import ObservabilityRetriever, SearchResult
from rag.indexer import IndexedChunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_indexer(n_results: int = 5, source: str = "log") -> object:
    """Cria um indexer falso que simula resultados FAISS sem ML real."""
    chunks = [
        IndexedChunk(
            text=f"log entry {i}",
            source=source,
            metadata={"level": "INFO", "service": f"svc-{i}", "deviation": float(i)},
        )
        for i in range(n_results)
    ]

    mock_model = MagicMock()
    import numpy as np
    mock_model.encode.return_value = np.array([[0.1] * 128], dtype=np.float32)

    mock_index = MagicMock()
    mock_index.ntotal = n_results
    # Simula distances e indices retornados pelo FAISS
    distances = [[0.9 - i * 0.1 for i in range(n_results)]]
    indices = [[i for i in range(n_results)]]
    mock_index.search.return_value = (distances, indices)

    indexer = MagicMock()
    indexer.model = mock_model
    indexer.index = mock_index
    indexer.chunks = chunks
    indexer._faiss_to_chunks = None  # evita MagicMock truthy no getattr
    return indexer


def _make_fake_reranker(return_results: list | None = None) -> object:
    """Cria um reranker falso que retorna resultados pré-definidos."""
    mock_reranker = MagicMock()
    if return_results is not None:
        mock_reranker.rerank.return_value = return_results
    return mock_reranker


# ---------------------------------------------------------------------------
# Story 8.3: Backward Compatibility
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBackwardCompatibility:
    """Verifica que ObservabilityRetriever sem reranker mantém comportamento V1."""

    def test_constructor_without_reranker_works(self):
        """ObservabilityRetriever(indexer) sem reranker deve inicializar sem erro."""
        indexer = _make_fake_indexer()
        retriever = ObservabilityRetriever(indexer)
        assert retriever.reranker is None

    def test_constructor_with_none_reranker_works(self):
        """ObservabilityRetriever(indexer, reranker=None) deve funcionar."""
        indexer = _make_fake_indexer()
        retriever = ObservabilityRetriever(indexer, reranker=None)
        assert retriever.reranker is None

    def test_search_without_reranker_returns_results(self):
        """search() sem reranker deve retornar resultados FAISS diretos."""
        indexer = _make_fake_indexer(n_results=5)
        retriever = ObservabilityRetriever(indexer)

        results = retriever.search("query test", top_k=5)

        assert isinstance(results, list)
        assert len(results) <= 5
        assert all(isinstance(r, SearchResult) for r in results)

    def test_search_without_reranker_respects_top_k(self):
        """search() sem reranker deve respeitar top_k exato."""
        indexer = _make_fake_indexer(n_results=10)
        retriever = ObservabilityRetriever(indexer)

        results = retriever.search("query", top_k=3)

        assert len(results) == 3

    def test_use_reranking_false_skips_reranker(self):
        """use_reranking=False deve retornar resultados FAISS sem chamar o reranker."""
        indexer = _make_fake_indexer(n_results=5)
        mock_reranker = _make_fake_reranker()
        retriever = ObservabilityRetriever(indexer, reranker=mock_reranker)

        results = retriever.search("query", top_k=5, use_reranking=False)

        mock_reranker.rerank.assert_not_called()
        assert isinstance(results, list)

    def test_search_empty_indexer_returns_empty(self):
        """search() com indexer sem índice deve retornar lista vazia."""
        indexer = MagicMock()
        indexer.index = None
        indexer.model = None
        retriever = ObservabilityRetriever(indexer)

        results = retriever.search("query")

        assert results == []


# ---------------------------------------------------------------------------
# Story 8.3: Reranker Integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRerankerIntegration:
    """Verifica que o reranker é chamado corretamente quando configurado."""

    def test_reranker_called_when_configured(self):
        """search() com reranker configurado deve chamar reranker.rerank()."""
        indexer = _make_fake_indexer(n_results=10)
        mock_reranker = _make_fake_reranker(return_results=[MagicMock()])
        retriever = ObservabilityRetriever(indexer, reranker=mock_reranker)

        retriever.search("query", top_k=5, use_reranking=True)

        mock_reranker.rerank.assert_called_once()

    def test_reranker_receives_top_k_argument(self):
        """reranker.rerank() deve receber top_k correto."""
        indexer = _make_fake_indexer(n_results=10)
        mock_reranker = _make_fake_reranker(return_results=[])
        retriever = ObservabilityRetriever(indexer, reranker=mock_reranker)

        retriever.search("query", top_k=7, use_reranking=True)

        call_kwargs = mock_reranker.rerank.call_args
        # Verifica que top_k=7 foi passado
        assert call_kwargs[1].get("top_k") == 7 or call_kwargs[0][2] == 7

    def test_reranker_exception_returns_raw_results(self):
        """Exceção no reranker deve resultar em warning e retorno de resultados FAISS brutos."""
        indexer = _make_fake_indexer(n_results=5)
        mock_reranker = MagicMock()
        mock_reranker.rerank.side_effect = RuntimeError("Reranker crashed")
        retriever = ObservabilityRetriever(indexer, reranker=mock_reranker)

        with warnings.catch_warnings(record=True) as caught_warnings:
            warnings.simplefilter("always")
            results = retriever.search("query", top_k=5, use_reranking=True)

        # Deve ter emitido um warning
        assert any("Reranker" in str(w.message) for w in caught_warnings)
        # Deve retornar resultados brutos
        assert isinstance(results, list)
        assert all(isinstance(r, SearchResult) for r in results)

    def test_reranker_exception_logs_warning(self):
        """Exceção no reranker deve logar warning via logger."""
        indexer = _make_fake_indexer(n_results=5)
        mock_reranker = MagicMock()
        mock_reranker.rerank.side_effect = ValueError("Bad reranker state")
        retriever = ObservabilityRetriever(indexer, reranker=mock_reranker)

        with patch("rag.retriever.logger") as mock_logger:
            retriever.search("query", top_k=5, use_reranking=True)
            mock_logger.warning.assert_called_once()

    def test_source_filter_skips_reranker(self):
        """source_filter ativo deve ignorar o reranker (comportamento V1 para filtros)."""
        indexer = _make_fake_indexer(n_results=5)
        mock_reranker = _make_fake_reranker(return_results=[])
        retriever = ObservabilityRetriever(indexer, reranker=mock_reranker)

        retriever.search("query", top_k=5, source_filter="log", use_reranking=True)

        mock_reranker.rerank.assert_not_called()


# ---------------------------------------------------------------------------
# Story 8.3: Config Wiring
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigWiring:
    """Verifica carregamento de service_criticality_map do config.yaml."""

    def test_config_yaml_can_be_loaded(self, tmp_path):
        """config.yaml com service_criticality_map deve ser carregável via yaml."""
        import yaml

        config_content = """
service_criticality_map:
  payment-svc: 1.0
  auth-svc: 0.9
  debug-svc: 0.1
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        with open(str(config_file)) as f:
            config = yaml.safe_load(f)

        assert "service_criticality_map" in config
        assert config["service_criticality_map"]["payment-svc"] == 1.0
        assert config["service_criticality_map"]["auth-svc"] == 0.9
        assert config["service_criticality_map"]["debug-svc"] == 0.1

    def test_service_criticality_map_from_config_wires_to_reranker(self, tmp_path):
        """Mapa de criticidade do config deve ser passado ao DomainReranker."""
        import yaml
        from rag.reranker import DomainReranker

        config_content = """
service_criticality_map:
  payment-svc: 1.0
  debug-svc: 0.1
"""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(config_content)

        with open(str(config_file)) as f:
            config = yaml.safe_load(f)

        criticality_map = config.get("service_criticality_map", {})
        reranker = DomainReranker(service_criticality_map=criticality_map)

        assert reranker.service_criticality_map["payment-svc"] == 1.0
        assert reranker.service_criticality_map["debug-svc"] == 0.1

    def test_project_config_yaml_has_expected_services(self):
        """config.yaml do projeto deve conter os serviços críticos esperados."""
        import os
        import yaml

        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(project_root, "config.yaml")

        assert os.path.isfile(config_path), "config.yaml deve existir na raiz do projeto"

        with open(config_path) as f:
            config = yaml.safe_load(f)

        assert "service_criticality_map" in config
        svc_map = config["service_criticality_map"]

        # Serviços obrigatórios
        assert "payment-svc" in svc_map
        assert "database" in svc_map
        assert svc_map["payment-svc"] == 1.0
        assert svc_map["debug-svc"] == 0.1


# ---------------------------------------------------------------------------
# Story 8.3: Integration tests (retriever + reranker real)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRetrieverRerankerIntegration:
    """Teste de integração com DomainReranker real (sem ML/FAISS)."""

    def test_retriever_with_domain_reranker_reorders_results(self):
        """Retriever com DomainReranker real deve reordenar resultados por sinais de domínio."""
        from rag.reranker import DomainReranker

        criticality_map = {"payment-svc": 1.0, "debug-svc": 0.1}
        reranker = DomainReranker(service_criticality_map=criticality_map)

        # Cria chunks com metadados realistas
        chunk_high = IndexedChunk(
            text="payment connection pool exhausted ERROR",
            source="metric",
            metadata={"service": "payment-svc", "deviation": 8.0, "level": "ERROR"},
        )
        chunk_low = IndexedChunk(
            text="debug service info log",
            source="log",
            metadata={"service": "debug-svc", "deviation": 0.5, "level": "INFO"},
        )

        # Simula resultados FAISS (score bruto baixo para payment-svc intencional)
        results = [
            SearchResult(chunk=chunk_low, score=0.8),   # FAISS diz low é melhor
            SearchResult(chunk=chunk_high, score=0.6),  # mas reranker deve inverter
        ]

        reranked = reranker.rerank(results, query="payment error", top_k=10)

        assert len(reranked) == 2
        # Após reranking, payment-svc deve estar no topo
        assert reranked[0].chunk.metadata["service"] == "payment-svc"
