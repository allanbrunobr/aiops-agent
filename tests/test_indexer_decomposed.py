from __future__ import annotations

"""Testes unitários para as funções extraídas de ObservabilityIndexerWithHistory.

Cobre _carregar_documentos e _embedar_e_indexar com mocks de faiss e
sentence-transformers para execução rápida sem dependências pesadas.
"""

from dataclasses import dataclass, field
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Fixtures auxiliares — objetos mínimos que imitam os tipos do pipeline
# ---------------------------------------------------------------------------


@dataclass
class _LogTemplate:
    level: str
    services: set
    template: str
    count: int
    first_seen: str
    last_seen: str


@dataclass
class _MetricAnomaly:
    service: str
    metric_name: str
    anomaly_type: str
    value: float
    mean: float
    deviation_sigma: float
    timestamp: str


@dataclass
class _SlowTrace:
    path: list
    total_duration_ms: float
    normal_duration_ms: float
    bottleneck_service: str
    bottleneck_duration_ms: float
    attributes: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Fixture do indexer com model mockado
# ---------------------------------------------------------------------------


@pytest.fixture
def indexer():
    """Retorna ObservabilityIndexerWithHistory com _load_model e model mockados."""
    # Mock de faiss e sentence_transformers para não exigir instalação pesada
    fake_model = MagicMock()
    fake_model.encode.return_value = np.random.default_rng(42).random((3, 384)).astype(np.float32)

    with patch("rag.indexer._model_cache", {}), patch("rag.indexer._embedding_cache", {}):
        from rag.indexer import ObservabilityIndexerWithHistory

        obj = ObservabilityIndexerWithHistory.__new__(ObservabilityIndexerWithHistory)
        obj.model_name = "all-MiniLM-L6-v2"
        obj.db_path = "data/incident_memory.db"
        obj.chunks = []
        obj.index = None
        obj.model = fake_model
        return obj


# ---------------------------------------------------------------------------
# Testes para _carregar_documentos
# ---------------------------------------------------------------------------


class TestCarregarDocumentos:
    """Testes unitários para _carregar_documentos."""

    def test_log_templates_geram_chunks(self, indexer):
        """Cada log template deve gerar exatamente 1 chunk com source='log'."""
        logs = [
            _LogTemplate("ERROR", {"svc-a"}, "connection refused", 5, "t0", "t1"),
            _LogTemplate("WARN", {"svc-b", "svc-c"}, "timeout", 2, "t2", "t3"),
        ]
        chunks = indexer._carregar_documentos(logs, [], [], include_history=False)

        assert len(chunks) == 2
        assert all(c.source == "log" for c in chunks)
        assert all(c.metadata["chunk_weight"] == 1.0 for c in chunks)

    def test_metric_anomalies_geram_chunks(self, indexer):
        """Cada anomalia de métrica deve gerar 1 chunk com source='metric'."""
        metrics = [
            _MetricAnomaly("svc-a", "cpu", "spike", 95.0, 40.0, 3.5, "2026-01-01"),
        ]
        chunks = indexer._carregar_documentos([], metrics, [], include_history=False)

        assert len(chunks) == 1
        assert chunks[0].source == "metric"
        assert chunks[0].metadata["chunk_weight"] == 1.0

    def test_slow_traces_geram_chunks(self, indexer):
        """Cada trace lento deve gerar 1 chunk com source='trace'."""
        traces = [
            _SlowTrace(["A", "B"], 500.0, 100.0, "B", 400.0),
        ]
        chunks = indexer._carregar_documentos([], [], traces, include_history=False)

        assert len(chunks) == 1
        assert chunks[0].source == "trace"
        assert "B" in chunks[0].text

    def test_include_history_false_nao_consulta_db(self, indexer):
        """include_history=False não deve tentar acessar o banco de dados."""
        with patch("rag.memory.IncidentMemoryDB") as mock_db:
            indexer._carregar_documentos([], [], [], include_history=False)
            mock_db.assert_not_called()

    def test_include_history_true_degradacao_gracioso_em_erro(self, indexer):
        """Erro ao acessar o banco não deve propagar — degradação graciosa."""
        with patch("rag.memory.IncidentMemoryDB", side_effect=Exception("db error")):
            # Não deve levantar exceção
            chunks = indexer._carregar_documentos([], [], [], include_history=True)
            assert isinstance(chunks, list)

    def test_chunks_historicos_tem_peso_0_5(self, indexer):
        """Chunks históricos devem ter chunk_weight=0.5 e is_historical=True."""
        import json

        mock_row = {
            "incident_id": "inc-1",
            "scenario": "cpu spike",
            "root_cause": "leak",
            "affected_services": json.dumps(["svc-a"]),
            "tags": json.dumps(["critical"]),
            "diagnosis_confidence": 0.9,
            "resolution_time_sec": 120.0,
        }
        mock_conn = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.return_value.fetchall.return_value = [mock_row]

        mock_db_instance = MagicMock()
        mock_db_instance._connect.return_value = mock_conn

        with patch("rag.memory.IncidentMemoryDB", return_value=mock_db_instance):
            chunks = indexer._carregar_documentos([], [], [], include_history=True)

        historical = [c for c in chunks if c.source == "historical"]
        assert len(historical) == 1
        assert historical[0].metadata["chunk_weight"] == 0.5
        assert historical[0].metadata["is_historical"] is True

    def test_servicos_ordenados_no_texto_de_log(self, indexer):
        """Serviços nos chunks de log devem aparecer em ordem alfabética."""
        logs = [_LogTemplate("INFO", {"z-svc", "a-svc"}, "pattern", 1, "t0", "t1")]
        chunks = indexer._carregar_documentos(logs, [], [], include_history=False)
        assert "a-svc, z-svc" in chunks[0].text


# ---------------------------------------------------------------------------
# Testes para _embedar_e_indexar
# ---------------------------------------------------------------------------


class TestEmbedarEIndexar:
    """Testes unitários para _embedar_e_indexar."""

    def test_lista_vazia_retorna_zero_e_index_none(self, indexer):
        """Lista vazia deve retornar 0 e deixar self.index como None."""
        import sys
        fake_faiss = MagicMock()
        with patch.dict(sys.modules, {"faiss": fake_faiss}):
            result = indexer._embedar_e_indexar([])
        assert result == 0
        assert indexer.index is None

    def test_retorna_numero_de_chunks(self, indexer):
        """Deve retornar o número exato de chunks indexados."""
        import sys
        from rag.indexer import IndexedChunk

        chunks = [
            IndexedChunk(text="log entry A", source="log", metadata={}),
            IndexedChunk(text="log entry B", source="log", metadata={}),
            IndexedChunk(text="metric X", source="metric", metadata={}),
        ]
        indexer.model.encode.return_value = np.random.default_rng(0).random((3, 384)).astype(np.float32)

        fake_faiss = MagicMock()
        fake_index = MagicMock()
        fake_faiss.IndexFlatIP.return_value = fake_index
        with patch.dict(sys.modules, {"faiss": fake_faiss}):
            result = indexer._embedar_e_indexar(chunks)

        assert result == 3

    def test_deduplicacao_textos_identicos(self, indexer):
        """Textos idênticos devem ser embedados uma única vez."""
        import sys
        from rag.indexer import IndexedChunk

        chunks = [
            IndexedChunk(text="mesmo texto", source="log", metadata={}),
            IndexedChunk(text="mesmo texto", source="log", metadata={}),
        ]
        indexer.model.encode.return_value = np.random.default_rng(1).random((1, 384)).astype(np.float32)

        fake_faiss = MagicMock()
        fake_index = MagicMock()
        fake_faiss.IndexFlatIP.return_value = fake_index
        with patch.dict(sys.modules, {"faiss": fake_faiss}):
            result = indexer._embedar_e_indexar(chunks)

        # encode chamado com 1 texto único
        call_args = indexer.model.encode.call_args[0][0]
        assert len(call_args) == 1
        assert result == 2  # retorna total de chunks, não únicos
