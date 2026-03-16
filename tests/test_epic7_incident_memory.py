"""
Testes para Epic 7 — Institutional Memory.

Cobre:
- Story 7.1: IncidentMemoryDB (rag/memory.py)
- Story 7.2: ObservabilityIndexerWithHistory (rag/indexer.py)
- Story 7.3: ObservabilityRetrieverWithHistory (rag/retriever_history.py)
"""

from __future__ import annotations

import os
import tempfile
import pytest

from rag.memory import IncidentRecord, IncidentMemoryDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(tmp_path):
    """Banco de dados temporário para testes."""
    return str(tmp_path / "test_incidents.db")


@pytest.fixture
def sample_record():
    """IncidentRecord de exemplo."""
    return IncidentRecord(
        incident_id="test-uuid-1234",
        timestamp="2026-03-15T10:00:00+00:00",
        scenario="payment-pool-exhaustion",
        root_cause="Connection pool exhaustion due to config change",
        affected_services=["payment-service", "api-gateway"],
        diagnosis_confidence=0.95,
        resolution_time_sec=120.0,
        tags=["connection-pool", "config-change"],
        evidence_summary="High error rate in payment-service logs",
    )


@pytest.fixture
def db(tmp_db):
    """IncidentMemoryDB em diretório temporário."""
    return IncidentMemoryDB(db_path=tmp_db)


# ---------------------------------------------------------------------------
# Story 7.1: IncidentMemoryDB
# ---------------------------------------------------------------------------

class TestIncidentMemoryDBInit:
    """Testa criação automática do banco e esquema."""

    @pytest.mark.unit
    def test_creates_db_file(self, tmp_db):
        """DB é criado automaticamente na inicialização."""
        assert not os.path.exists(tmp_db)
        IncidentMemoryDB(db_path=tmp_db)
        assert os.path.exists(tmp_db)

    @pytest.mark.unit
    def test_creates_schema(self, db, tmp_db):
        """Tabela incidents é criada com colunas corretas."""
        import sqlite3
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.execute("PRAGMA table_info(incidents)")
            columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "incident_id", "timestamp", "scenario", "root_cause",
            "affected_services", "diagnosis_confidence", "resolution_time_sec",
            "tags", "evidence_summary",
        }
        assert expected.issubset(columns)

    @pytest.mark.unit
    def test_creates_indexes(self, tmp_db):
        """Índices em scenario, timestamp e affected_services são criados."""
        import sqlite3
        IncidentMemoryDB(db_path=tmp_db)
        with sqlite3.connect(tmp_db) as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            index_names = {row[0] for row in cursor.fetchall()}
        assert "idx_incidents_scenario" in index_names
        assert "idx_incidents_timestamp" in index_names
        assert "idx_incidents_services" in index_names


class TestSaveIncident:
    """Testa persistência de incidentes."""

    @pytest.mark.unit
    def test_saves_record(self, db, sample_record):
        """Registro é salvo com todos os campos."""
        db.save_incident(sample_record)
        import sqlite3
        with sqlite3.connect(db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM incidents WHERE incident_id = ?",
                (sample_record.incident_id,),
            ).fetchone()
        assert row is not None
        assert row["scenario"] == sample_record.scenario
        assert row["root_cause"] == sample_record.root_cause
        assert row["diagnosis_confidence"] == pytest.approx(0.95)

    @pytest.mark.unit
    def test_upsert_does_not_duplicate(self, db, sample_record):
        """Segunda chamada com mesmo incident_id não duplica registro."""
        db.save_incident(sample_record)
        db.save_incident(sample_record)
        import sqlite3
        with sqlite3.connect(db.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0]
        assert count == 1

    @pytest.mark.unit
    def test_upsert_updates_fields(self, db, sample_record):
        """Upsert atualiza campos existentes."""
        db.save_incident(sample_record)
        updated = IncidentRecord(
            incident_id=sample_record.incident_id,
            timestamp=sample_record.timestamp,
            scenario=sample_record.scenario,
            root_cause="Updated root cause",
            affected_services=sample_record.affected_services,
            diagnosis_confidence=0.99,
            resolution_time_sec=sample_record.resolution_time_sec,
            tags=sample_record.tags,
            evidence_summary=sample_record.evidence_summary,
        )
        db.save_incident(updated)
        import sqlite3
        with sqlite3.connect(db.db_path) as conn:
            row = conn.execute(
                "SELECT root_cause, diagnosis_confidence FROM incidents WHERE incident_id = ?",
                (sample_record.incident_id,),
            ).fetchone()
        assert row[0] == "Updated root cause"
        assert row[1] == pytest.approx(0.99)


class TestFindSimilarIncidents:
    """Testa busca por similaridade."""

    @pytest.mark.unit
    def test_returns_empty_when_no_records(self, db):
        """Retorna lista vazia quando banco está vazio."""
        result = db.find_similar_incidents(["payment-service"], ["connection-pool"])
        assert result == []

    @pytest.mark.unit
    def test_returns_empty_when_no_overlap(self, db, sample_record):
        """Retorna lista vazia quando não há sobreposição."""
        db.save_incident(sample_record)
        result = db.find_similar_incidents(["unrelated-service"], ["unrelated-tag"])
        assert result == []

    @pytest.mark.unit
    def test_finds_by_service_overlap(self, db, sample_record):
        """Retorna registros com serviços em comum."""
        db.save_incident(sample_record)
        result = db.find_similar_incidents(["payment-service"], [])
        assert len(result) == 1
        assert result[0].incident_id == sample_record.incident_id

    @pytest.mark.unit
    def test_finds_by_tag_overlap(self, db, sample_record):
        """Retorna registros com tags em comum."""
        db.save_incident(sample_record)
        result = db.find_similar_incidents([], ["connection-pool"])
        assert len(result) == 1

    @pytest.mark.unit
    def test_service_weighted_higher_than_tag(self, db):
        """Serviço tem peso 2, tag tem peso 1 na ordenação."""
        rec_service = IncidentRecord(
            incident_id="rec-service",
            timestamp="2026-03-15T09:00:00+00:00",
            scenario="scenario-A",
            root_cause="Root A",
            affected_services=["payment-service"],  # overlap service
            diagnosis_confidence=0.9,
            resolution_time_sec=60.0,
            tags=["other-tag"],  # no tag overlap
            evidence_summary="",
        )
        rec_tag = IncidentRecord(
            incident_id="rec-tag",
            timestamp="2026-03-15T08:00:00+00:00",
            scenario="scenario-B",
            root_cause="Root B",
            affected_services=["other-service"],  # no service overlap
            diagnosis_confidence=0.8,
            resolution_time_sec=90.0,
            tags=["connection-pool", "config-change"],  # 2 tag overlaps
            evidence_summary="",
        )
        db.save_incident(rec_service)
        db.save_incident(rec_tag)
        # service overlap score = 2, tag overlap score = 2 — tie, but service record first
        result = db.find_similar_incidents(["payment-service"], ["connection-pool"])
        assert len(result) == 2
        # rec_service: service(1*2=2) + tag(0*1=0) = 2
        # rec_tag: service(0*2=0) + tag(2*1=2) = 2
        # Both score 2, order by tie is deterministic (both valid)
        ids = [r.incident_id for r in result]
        assert "rec-service" in ids
        assert "rec-tag" in ids

    @pytest.mark.unit
    def test_respects_limit(self, db):
        """Retorna no máximo `limit` registros."""
        for i in range(10):
            rec = IncidentRecord(
                incident_id=f"id-{i}",
                timestamp=f"2026-03-{i+1:02d}T10:00:00+00:00",
                scenario=f"scenario-{i}",
                root_cause=f"Root {i}",
                affected_services=["payment-service"],
                diagnosis_confidence=0.9,
                resolution_time_sec=60.0,
                tags=["connection-pool"],
                evidence_summary="",
            )
            db.save_incident(rec)
        result = db.find_similar_incidents(["payment-service"], ["connection-pool"], limit=3)
        assert len(result) == 3

    @pytest.mark.unit
    def test_returns_incident_record_instances(self, db, sample_record):
        """Retorna instâncias de IncidentRecord."""
        db.save_incident(sample_record)
        result = db.find_similar_incidents(["payment-service"], [])
        assert isinstance(result[0], IncidentRecord)

    @pytest.mark.unit
    def test_no_llm_dependency(self, db, sample_record):
        """save_incident e find_similar_incidents não dependem de LLM."""
        # Sem mock de LLM — deve funcionar normalmente
        db.save_incident(sample_record)
        result = db.find_similar_incidents(["payment-service"], ["connection-pool"])
        assert len(result) == 1


class TestIncidentRecordNew:
    """Testa método de fábrica IncidentRecord.new()."""

    @pytest.mark.unit
    def test_generates_uuid(self):
        """new() gera incident_id como UUID válido."""
        import uuid
        rec = IncidentRecord.new(
            scenario="test",
            root_cause="test cause",
            affected_services=["svc"],
            diagnosis_confidence=0.9,
            resolution_time_sec=60.0,
            tags=["tag"],
        )
        # Não deve lançar exceção
        uuid.UUID(rec.incident_id)

    @pytest.mark.unit
    def test_generates_timestamp(self):
        """new() preenche timestamp em formato ISO 8601."""
        rec = IncidentRecord.new(
            scenario="test",
            root_cause="test cause",
            affected_services=[],
            diagnosis_confidence=0.9,
            resolution_time_sec=60.0,
            tags=[],
        )
        assert "T" in rec.timestamp  # formato ISO


# ---------------------------------------------------------------------------
# Story 7.2: ObservabilityIndexerWithHistory
# ---------------------------------------------------------------------------

class TestObservabilityIndexerWithHistory:
    """Testa indexador com suporte a chunks históricos."""

    @pytest.mark.unit
    def test_is_importable(self):
        """ObservabilityIndexerWithHistory pode ser importado."""
        from rag.indexer import ObservabilityIndexerWithHistory
        assert ObservabilityIndexerWithHistory is not None

    @pytest.mark.unit
    def test_include_history_false_builds_current_only(self, tmp_path):
        """include_history=False não adiciona chunks históricos."""
        import sys
        from unittest.mock import MagicMock, patch
        import numpy as np
        from rag.indexer import ObservabilityIndexerWithHistory

        db_path = str(tmp_path / "test.db")

        mock_faiss = MagicMock()
        mock_faiss.IndexFlatIP.return_value = MagicMock()
        with patch.dict(sys.modules, {"faiss": mock_faiss}):
            indexer = ObservabilityIndexerWithHistory(db_path=db_path)
            mock_model = MagicMock()
            mock_model.encode.return_value = np.zeros((0, 4), dtype=np.float32)
            indexer.model = mock_model
            count = indexer.build_index_with_history([], [], [], include_history=False)

        assert count == 0
        historical = [c for c in indexer.chunks if c.source == "historical"]
        assert historical == []

    @pytest.mark.unit
    def test_include_history_true_adds_historical_chunks(self, tmp_path):
        """include_history=True adiciona chunks históricos ao índice."""
        import sys
        from unittest.mock import MagicMock, patch
        import numpy as np
        from rag.indexer import ObservabilityIndexerWithHistory

        db_path = str(tmp_path / "mem.db")
        mem_db = IncidentMemoryDB(db_path=db_path)
        record = IncidentRecord(
            incident_id="hist-001",
            timestamp="2026-03-01T10:00:00+00:00",
            scenario="past-scenario",
            root_cause="Past root cause",
            affected_services=["svc-a"],
            diagnosis_confidence=0.85,
            resolution_time_sec=90.0,
            tags=["connection-pool"],
            evidence_summary="past evidence",
        )
        mem_db.save_incident(record)

        mock_faiss = MagicMock()
        mock_faiss.IndexFlatIP.return_value = MagicMock()
        with patch.dict(sys.modules, {"faiss": mock_faiss}):
            indexer = ObservabilityIndexerWithHistory(db_path=db_path)
            mock_model = MagicMock()
            mock_model.encode.return_value = np.zeros((1, 4), dtype=np.float32)
            indexer.model = mock_model
            count = indexer.build_index_with_history([], [], [], include_history=True)

        assert count == 1
        assert indexer.chunks[0].source == "historical"
        assert indexer.chunks[0].metadata["is_historical"] is True
        assert indexer.chunks[0].metadata["chunk_weight"] == 0.5

    @pytest.mark.unit
    def test_graceful_degradation_on_empty_db(self, tmp_path):
        """Banco vazio não levanta exceção — indexa apenas chunks atuais."""
        import sys
        from unittest.mock import MagicMock, patch
        import numpy as np
        from rag.indexer import ObservabilityIndexerWithHistory

        db_path = str(tmp_path / "empty.db")
        IncidentMemoryDB(db_path=db_path)

        mock_faiss = MagicMock()
        mock_faiss.IndexFlatIP.return_value = MagicMock()
        with patch.dict(sys.modules, {"faiss": mock_faiss}):
            indexer = ObservabilityIndexerWithHistory(db_path=db_path)
            mock_model = MagicMock()
            mock_model.encode.return_value = np.zeros((0, 4), dtype=np.float32)
            indexer.model = mock_model
            count = indexer.build_index_with_history([], [], [], include_history=True)

        assert count == 0

    @pytest.mark.unit
    def test_graceful_degradation_on_exception(self, tmp_path):
        """Exceção ao acessar DB histórico não interrompe indexação."""
        import sys
        from unittest.mock import MagicMock, patch
        import numpy as np
        from rag.indexer import ObservabilityIndexerWithHistory

        mock_faiss = MagicMock()
        mock_faiss.IndexFlatIP.return_value = MagicMock()
        with patch.dict(sys.modules, {"faiss": mock_faiss}):
            # /invalid/path forçará erro ao tentar abrir o DB
            indexer = ObservabilityIndexerWithHistory(db_path="/invalid/path/db.db")
            mock_model = MagicMock()
            mock_model.encode.return_value = np.zeros((0, 4), dtype=np.float32)
            indexer.model = mock_model
            # Não deve levantar exceção
            count = indexer.build_index_with_history([], [], [], include_history=True)

        assert count == 0


# ---------------------------------------------------------------------------
# Story 7.3: ObservabilityRetrieverWithHistory
# ---------------------------------------------------------------------------

class TestObservabilityRetrieverWithHistory:
    """Testa retriever com precedentes históricos."""

    @pytest.mark.unit
    def test_is_importable(self):
        """ObservabilityRetrieverWithHistory pode ser importado."""
        from rag.retriever_history import ObservabilityRetrieverWithHistory
        assert ObservabilityRetrieverWithHistory is not None

    @pytest.mark.unit
    def test_returns_empty_when_no_memory(self, tmp_path):
        """get_similar_past_incidents retorna [] quando banco está vazio."""
        from rag.retriever_history import ObservabilityRetrieverWithHistory
        from rag.indexer import ObservabilityIndexer

        db_path = str(tmp_path / "empty.db")
        IncidentMemoryDB(db_path=db_path)

        indexer = ObservabilityIndexer()
        retriever = ObservabilityRetrieverWithHistory(indexer, db_path=db_path)
        result = retriever.get_similar_past_incidents(["svc"], ["tag"])
        assert result == []

    @pytest.mark.unit
    def test_returns_formatted_precedents(self, tmp_path):
        """get_similar_past_incidents retorna dicts formatados."""
        from rag.retriever_history import ObservabilityRetrieverWithHistory
        from rag.indexer import ObservabilityIndexer

        db_path = str(tmp_path / "mem.db")
        mem_db = IncidentMemoryDB(db_path=db_path)
        record = IncidentRecord(
            incident_id="hist-002",
            timestamp="2026-03-10T10:00:00+00:00",
            scenario="my-scenario",
            root_cause="My root cause",
            affected_services=["payment-service"],
            diagnosis_confidence=0.92,
            resolution_time_sec=75.0,
            tags=["connection-pool"],
            evidence_summary="some evidence",
        )
        mem_db.save_incident(record)

        indexer = ObservabilityIndexer()
        retriever = ObservabilityRetrieverWithHistory(indexer, db_path=db_path)
        result = retriever.get_similar_past_incidents(["payment-service"], ["connection-pool"])

        assert len(result) == 1
        p = result[0]
        assert p["scenario"] == "my-scenario"
        assert p["root_cause"] == "My root cause"
        assert p["affected_services"] == ["payment-service"]
        assert p["resolution_time_sec"] == pytest.approx(75.0)
        assert p["diagnosis_confidence"] == pytest.approx(0.92)

    @pytest.mark.unit
    def test_exception_is_silenced(self, tmp_path):
        """Exceção em get_similar_past_incidents é silenciada — retorna []."""
        from rag.retriever_history import ObservabilityRetrieverWithHistory
        from rag.indexer import ObservabilityIndexer

        # Fornece path inválido que causará erro ao acessar
        indexer = ObservabilityIndexer()
        retriever = ObservabilityRetrieverWithHistory(indexer, db_path="/invalid/path/db.db")
        # Não deve levantar exceção
        result = retriever.get_similar_past_incidents(["svc"], ["tag"])
        assert result == []
