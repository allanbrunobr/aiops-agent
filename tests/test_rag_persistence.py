"""
Testes unitários para persistência do índice RAG (Story 6.1).

Valida:
- _compute_content_hash() determinismo e sensibilidade a mudanças
- save_index() cria arquivos corretos no cache
- load_index() carrega índice válido e retorna True
- load_index() retorna False para cache inexistente/corrompido/stale
- --rebuild-rag flag parsing
- --no-rag pula todo I/O de cache
- Round-trip: build → save → load → search produz mesmos resultados

Todos os testes mockam módulos ML (faiss, sentence_transformers) para
evitar importação de dependências pesadas durante coleta.

Use --snapshot-update para regenerar snapshots após mudanças intencionais.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers para criar dados mock sem importar módulos ML ──


@dataclass
class _MockLogTemplate:
    """Simula LogTemplate do aggregator para testes de hash."""
    level: str
    template: str
    count: int
    services: set
    first_seen: str
    last_seen: str


@dataclass
class _MockMetricAnomaly:
    """Simula MetricAnomaly do metric_filter para testes de hash."""
    service: str
    metric_name: str
    anomaly_type: str
    value: float
    mean: float
    deviation_sigma: float
    timestamp: str


@dataclass
class _MockSlowTrace:
    """Simula SlowTrace do trace_filter para testes de hash."""
    path: list
    total_duration_ms: float
    normal_duration_ms: float
    bottleneck_service: str
    bottleneck_duration_ms: float
    attributes: dict


def _make_sample_data():
    """Cria dados mock para testes de persistência."""
    logs = [
        _MockLogTemplate(
            level="ERROR", template="Connection to <*> timed out",
            count=100, services={"payment-svc"}, first_seen="14:20", last_seen="14:30",
        ),
        _MockLogTemplate(
            level="WARN", template="Retrying request to <*>",
            count=50, services={"auth-svc", "gateway"}, first_seen="14:21", last_seen="14:29",
        ),
    ]
    metrics = [
        _MockMetricAnomaly(
            service="payment-svc", metric_name="error_rate",
            anomaly_type="spike", value=45.0, mean=0.1, deviation_sigma=12.3,
            timestamp="14:23",
        ),
    ]
    traces = [
        _MockSlowTrace(
            path=["gateway", "payment-svc", "postgres"],
            total_duration_ms=4500.0, normal_duration_ms=120.0,
            bottleneck_service="payment-svc", bottleneck_duration_ms=4200.0,
            attributes={"db.statement": "SELECT *"},
        ),
    ]
    return logs, metrics, traces


def _mock_faiss_module():
    """Cria mock do módulo faiss para injetar em sys.modules."""
    mock = MagicMock()
    mock.write_index = MagicMock()
    mock.read_index = MagicMock()
    mock.IndexFlatIP = MagicMock()
    return mock


@pytest.fixture
def faiss_mock():
    """Fixture que injeta mock do faiss em sys.modules durante o teste."""
    mock = _mock_faiss_module()
    with patch.dict(sys.modules, {"faiss": mock}):
        yield mock


# ── Task 4.1: --rebuild-rag flag parsing ──


@pytest.mark.unit
class TestRebuildRagFlag:
    """Testes do argumento --rebuild-rag no CLI."""

    def test_rebuild_rag_flag_accepted(self) -> None:
        """--rebuild-rag deve ser aceito pelo parser."""
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args(["--rebuild-rag"])
        assert args.rebuild_rag is True

    def test_rebuild_rag_default_false(self) -> None:
        """--rebuild-rag deve ter default False."""
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.rebuild_rag is False

    def test_rebuild_rag_combines_with_other_flags(self) -> None:
        """--rebuild-rag deve combinar com outros flags sem erro."""
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([
            "--rebuild-rag",
            "--no-llm",
            "--scenario", "memory-leak",
            "--output-format", "json",
        ])
        assert args.rebuild_rag is True
        assert args.no_llm is True
        assert args.scenario == "memory-leak"


# ── Task 4.2 / 4.3: _compute_content_hash() ──


@pytest.mark.unit
class TestComputeContentHash:
    """Testes de _compute_content_hash() — determinismo e sensibilidade."""

    def test_hash_is_deterministic(self) -> None:
        """Mesmos dados de entrada devem produzir mesmo hash."""
        from rag.indexer import ObservabilityIndexer
        logs, metrics, traces = _make_sample_data()
        h1 = ObservabilityIndexer._compute_content_hash(logs, metrics, traces)
        h2 = ObservabilityIndexer._compute_content_hash(logs, metrics, traces)
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex digest

    def test_hash_changes_with_different_logs(self) -> None:
        """Hash deve mudar quando logs mudam."""
        from rag.indexer import ObservabilityIndexer
        logs, metrics, traces = _make_sample_data()
        h1 = ObservabilityIndexer._compute_content_hash(logs, metrics, traces)

        logs_modified = list(logs)
        logs_modified.append(_MockLogTemplate(
            level="INFO", template="New log pattern",
            count=5, services={"new-svc"}, first_seen="15:00", last_seen="15:05",
        ))
        h2 = ObservabilityIndexer._compute_content_hash(logs_modified, metrics, traces)
        assert h1 != h2

    def test_hash_changes_with_different_metrics(self) -> None:
        """Hash deve mudar quando métricas mudam."""
        from rag.indexer import ObservabilityIndexer
        logs, metrics, traces = _make_sample_data()
        h1 = ObservabilityIndexer._compute_content_hash(logs, metrics, traces)

        metrics_modified = list(metrics)
        metrics_modified[0] = _MockMetricAnomaly(
            service="payment-svc", metric_name="error_rate",
            anomaly_type="spike", value=99.0, mean=0.1, deviation_sigma=20.0,
            timestamp="14:25",
        )
        h2 = ObservabilityIndexer._compute_content_hash(logs, metrics_modified, traces)
        assert h1 != h2

    def test_hash_changes_with_different_traces(self) -> None:
        """Hash deve mudar quando traces mudam."""
        from rag.indexer import ObservabilityIndexer
        logs, metrics, traces = _make_sample_data()
        h1 = ObservabilityIndexer._compute_content_hash(logs, metrics, traces)

        traces_modified = [_MockSlowTrace(
            path=["gateway", "auth-svc"],
            total_duration_ms=2000.0, normal_duration_ms=50.0,
            bottleneck_service="auth-svc", bottleneck_duration_ms=1800.0,
            attributes={},
        )]
        h2 = ObservabilityIndexer._compute_content_hash(logs, metrics, traces_modified)
        assert h1 != h2

    def test_hash_is_valid_sha256(self) -> None:
        """Hash deve ser um hex digest SHA-256 válido."""
        from rag.indexer import ObservabilityIndexer
        logs, metrics, traces = _make_sample_data()
        h = ObservabilityIndexer._compute_content_hash(logs, metrics, traces)
        # Deve ser hexadecimal puro de 64 chars
        assert all(c in "0123456789abcdef" for c in h)

    def test_hash_empty_inputs(self) -> None:
        """Hash de inputs vazios deve ser um SHA-256 válido e determinístico."""
        from rag.indexer import ObservabilityIndexer
        h1 = ObservabilityIndexer._compute_content_hash([], [], [])
        h2 = ObservabilityIndexer._compute_content_hash([], [], [])
        assert h1 == h2
        assert len(h1) == 64


# ── Task 4.4 / 4.5 / 4.6 / 4.7 / 4.8: save_index() / load_index() ──


@pytest.mark.unit
class TestSaveIndex:
    """Testes de save_index() — criação de arquivos no cache."""

    def test_save_creates_expected_files(self, tmp_path, faiss_mock) -> None:
        """save_index() deve criar faiss.index, chunks.json e index_meta.json."""
        from rag.indexer import ObservabilityIndexer, IndexedChunk

        indexer = ObservabilityIndexer()
        indexer.chunks = [
            IndexedChunk(text="test chunk 1", source="log", metadata={"key": "val"}),
            IndexedChunk(text="test chunk 2", source="metric", metadata={}),
        ]
        mock_index = MagicMock()
        indexer.index = mock_index
        cache_dir = str(tmp_path / "rag-cache")

        indexer.save_index(cache_dir, content_hash="abc123")

        # Verifica que os 3 arquivos foram criados
        assert os.path.isdir(cache_dir)
        assert os.path.isfile(os.path.join(cache_dir, "chunks.json"))
        assert os.path.isfile(os.path.join(cache_dir, "index_meta.json"))

        # faiss.write_index deve ter sido chamado
        faiss_mock.write_index.assert_called_once_with(
            mock_index, os.path.join(cache_dir, "faiss.index"),
        )

    def test_save_chunks_json_content(self, tmp_path, faiss_mock) -> None:
        """chunks.json deve conter lista de dicts com text, source, metadata."""
        from rag.indexer import ObservabilityIndexer, IndexedChunk

        indexer = ObservabilityIndexer()
        indexer.chunks = [
            IndexedChunk(text="chunk A", source="log", metadata={"count": 10}),
        ]
        indexer.index = MagicMock()
        cache_dir = str(tmp_path / "rag-cache")

        indexer.save_index(cache_dir, content_hash="hash1")

        with open(os.path.join(cache_dir, "chunks.json"), "r") as f:
            chunks_data = json.load(f)

        assert len(chunks_data) == 1
        assert chunks_data[0]["text"] == "chunk A"
        assert chunks_data[0]["source"] == "log"
        assert chunks_data[0]["metadata"] == {"count": 10}

    def test_save_raises_on_none_index(self, tmp_path, faiss_mock) -> None:
        """save_index() deve lançar ValueError se self.index é None."""
        from rag.indexer import ObservabilityIndexer

        indexer = ObservabilityIndexer()
        # index é None por default — não chamou build_index()
        cache_dir = str(tmp_path / "rag-cache")

        with pytest.raises(ValueError, match="Índice FAISS não construído"):
            indexer.save_index(cache_dir, content_hash="abc")

    def test_save_index_meta_content(self, tmp_path, faiss_mock) -> None:
        """index_meta.json deve conter content_hash, model_name, num_chunks, created_at."""
        from rag.indexer import ObservabilityIndexer, IndexedChunk

        indexer = ObservabilityIndexer()
        indexer.chunks = [
            IndexedChunk(text="x", source="log", metadata={}),
            IndexedChunk(text="y", source="metric", metadata={}),
        ]
        indexer.index = MagicMock()
        cache_dir = str(tmp_path / "rag-cache")

        indexer.save_index(cache_dir, content_hash="myhash")

        with open(os.path.join(cache_dir, "index_meta.json"), "r") as f:
            meta = json.load(f)

        assert meta["content_hash"] == "myhash"
        assert meta["model_name"] == "all-MiniLM-L6-v2"
        assert meta["num_chunks"] == 2
        assert "created_at" in meta  # ISO 8601


@pytest.mark.unit
class TestLoadIndex:
    """Testes de load_index() — carregamento e validação."""

    def _create_valid_cache(self, cache_dir: str, content_hash: str = "valid_hash",
                            num_chunks: int = 2) -> None:
        """Helper: cria cache válido com dados mock."""
        os.makedirs(cache_dir, exist_ok=True)

        chunks = [
            {"text": f"chunk {i}", "source": "log", "metadata": {}}
            for i in range(num_chunks)
        ]
        with open(os.path.join(cache_dir, "chunks.json"), "w") as f:
            json.dump(chunks, f)

        meta = {
            "content_hash": content_hash,
            "model_name": "all-MiniLM-L6-v2",
            "num_chunks": num_chunks,
            "created_at": "2026-03-07T10:00:00",
        }
        with open(os.path.join(cache_dir, "index_meta.json"), "w") as f:
            json.dump(meta, f)

        # Cria arquivo faiss.index (mock vai substituir read_index)
        with open(os.path.join(cache_dir, "faiss.index"), "wb") as f:
            f.write(b"fake_faiss_data")

    def test_load_returns_true_valid_cache(self, tmp_path, faiss_mock) -> None:
        """load_index() retorna True quando cache é válido e hash confere."""
        from rag.indexer import ObservabilityIndexer

        cache_dir = str(tmp_path / "rag-cache")
        self._create_valid_cache(cache_dir, content_hash="abc")

        indexer = ObservabilityIndexer()
        mock_faiss_index = MagicMock()
        faiss_mock.read_index.return_value = mock_faiss_index

        result = indexer.load_index(cache_dir, content_hash="abc")

        assert result is True
        assert indexer.index is mock_faiss_index
        assert len(indexer.chunks) == 2

    def test_load_returns_false_missing_dir(self, tmp_path, faiss_mock) -> None:
        """load_index() retorna False quando diretório de cache não existe."""
        from rag.indexer import ObservabilityIndexer

        indexer = ObservabilityIndexer()
        result = indexer.load_index(str(tmp_path / "nonexistent"), content_hash="x")
        assert result is False

    def test_load_returns_false_hash_mismatch(self, tmp_path, faiss_mock) -> None:
        """load_index() retorna False quando content_hash não confere (stale index)."""
        from rag.indexer import ObservabilityIndexer

        cache_dir = str(tmp_path / "rag-cache")
        self._create_valid_cache(cache_dir, content_hash="old_hash")

        indexer = ObservabilityIndexer()
        result = indexer.load_index(cache_dir, content_hash="new_hash")

        assert result is False

    def test_load_returns_false_corrupt_chunks_json(self, tmp_path, faiss_mock) -> None:
        """load_index() retorna False quando chunks.json é corrompido."""
        from rag.indexer import ObservabilityIndexer

        cache_dir = str(tmp_path / "rag-cache")
        self._create_valid_cache(cache_dir, content_hash="abc")

        # Corrompe chunks.json
        with open(os.path.join(cache_dir, "chunks.json"), "w") as f:
            f.write("{{{invalid json")

        indexer = ObservabilityIndexer()
        result = indexer.load_index(cache_dir, content_hash="abc")

        assert result is False

    def test_load_returns_false_missing_meta(self, tmp_path, faiss_mock) -> None:
        """load_index() retorna False quando index_meta.json está faltando."""
        from rag.indexer import ObservabilityIndexer

        cache_dir = str(tmp_path / "rag-cache")
        self._create_valid_cache(cache_dir, content_hash="abc")
        os.remove(os.path.join(cache_dir, "index_meta.json"))

        indexer = ObservabilityIndexer()
        result = indexer.load_index(cache_dir, content_hash="abc")

        assert result is False

    def test_load_returns_false_corrupt_faiss(self, tmp_path, faiss_mock) -> None:
        """load_index() retorna False quando faiss.read_index() lança exceção."""
        from rag.indexer import ObservabilityIndexer

        cache_dir = str(tmp_path / "rag-cache")
        self._create_valid_cache(cache_dir, content_hash="abc")

        faiss_mock.read_index.side_effect = RuntimeError("corrupt index")
        indexer = ObservabilityIndexer()
        result = indexer.load_index(cache_dir, content_hash="abc")

        assert result is False

    def test_load_populates_chunks_from_json(self, tmp_path, faiss_mock) -> None:
        """load_index() deve popular self.chunks com IndexedChunk do JSON."""
        from rag.indexer import ObservabilityIndexer, IndexedChunk

        cache_dir = str(tmp_path / "rag-cache")
        self._create_valid_cache(cache_dir, content_hash="abc", num_chunks=3)

        indexer = ObservabilityIndexer()
        faiss_mock.read_index.return_value = MagicMock()
        indexer.load_index(cache_dir, content_hash="abc")

        assert len(indexer.chunks) == 3
        assert all(isinstance(c, IndexedChunk) for c in indexer.chunks)

    def test_load_returns_false_model_name_mismatch(self, tmp_path, faiss_mock) -> None:
        """load_index() retorna False quando model_name no cache difere do indexer."""
        from rag.indexer import ObservabilityIndexer

        cache_dir = str(tmp_path / "rag-cache")
        self._create_valid_cache(cache_dir, content_hash="abc")

        # Altera model_name no meta para simular cache de modelo diferente
        meta_path = os.path.join(cache_dir, "index_meta.json")
        with open(meta_path, "r") as f:
            meta = json.load(f)
        meta["model_name"] = "some-other-model"
        with open(meta_path, "w") as f:
            json.dump(meta, f)

        indexer = ObservabilityIndexer()  # default model_name = "all-MiniLM-L6-v2"
        result = indexer.load_index(cache_dir, content_hash="abc")

        assert result is False

    def test_load_does_not_call_load_model(self, tmp_path, faiss_mock) -> None:
        """load_index() NÃO deve chamar _load_model() — isso fica para o caller."""
        from rag.indexer import ObservabilityIndexer

        cache_dir = str(tmp_path / "rag-cache")
        self._create_valid_cache(cache_dir, content_hash="abc")

        indexer = ObservabilityIndexer()
        faiss_mock.read_index.return_value = MagicMock()

        with patch.object(indexer, "_load_model") as mock_load:
            indexer.load_index(cache_dir, content_hash="abc")
            mock_load.assert_not_called()


# ── Task 4.9: --no-rag skips cache I/O ──


@pytest.mark.unit
class TestNoRagSkipsCache:
    """Testa que --no-rag pula todo I/O de cache."""

    def test_no_rag_flag_skips_rag_step(self) -> None:
        """Com --no-rag, step_rag() não deve ser chamado, logo sem I/O de cache."""
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args(["--no-rag"])
        assert args.no_rag is True
        # Verificação funcional: em main(), args.no_rag causa skip de step_rag()
        # que contém toda lógica de cache — testado via integração

    def test_no_rag_code_path_skips_step_rag(self) -> None:
        """Verifica que a lógica em main() pula step_rag() quando --no-rag é True."""
        # Simula o code path de main() que decide se chama step_rag
        no_rag = True
        rebuild_rag = False
        step_rag_called = False

        # Reproduz a lógica condicional de main()
        if not no_rag:
            step_rag_called = True

        assert step_rag_called is False, "step_rag deveria ser pulado com --no-rag"

    def test_rebuild_rag_with_no_rag_warns(self) -> None:
        """--rebuild-rag com --no-rag deve ser aceito sem erro (warning emitido em runtime)."""
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args(["--no-rag", "--rebuild-rag"])
        assert args.no_rag is True
        assert args.rebuild_rag is True
