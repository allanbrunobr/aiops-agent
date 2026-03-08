"""Testes unitarios para selecao de data source via CLI.

Valida o flag --data-source (simulated/databricks), exclusao mutua
com --scenario, factory de conectores Databricks, fallback gracioso
em caso de falha, e preservacao de NFR19 (zero network calls em
modo simulated).
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest


# ---- Helpers ----

def _parse_args(args_list: list[str]):
    """Roda build_arg_parser().parse_args() com a lista fornecida."""
    from main import build_arg_parser
    parser = build_arg_parser()
    return parser.parse_args(args_list)


# ===========================================================================
# Task 1: --data-source CLI flag
# ===========================================================================

class TestDataSourceArgument:
    """Testes para o argumento --data-source no CLI."""

    @pytest.mark.unit
    def test_default_data_source_is_simulated(self):
        """Sem --data-source, default e 'simulated'."""
        args = _parse_args([])
        assert args.data_source == "simulated"

    @pytest.mark.unit
    def test_data_source_simulated_explicit(self):
        """--data-source simulated e aceito."""
        args = _parse_args(["--data-source", "simulated"])
        assert args.data_source == "simulated"

    @pytest.mark.unit
    def test_data_source_databricks(self):
        """--data-source databricks e aceito."""
        args = _parse_args(["--data-source", "databricks"])
        assert args.data_source == "databricks"

    @pytest.mark.unit
    def test_data_source_invalid_choice(self):
        """--data-source invalido gera SystemExit."""
        with pytest.raises(SystemExit):
            _parse_args(["--data-source", "invalid"])

    @pytest.mark.unit
    def test_data_source_with_other_flags(self):
        """--data-source combina com --no-llm, --no-rag, --output-dir."""
        args = _parse_args([
            "--data-source", "databricks",
            "--no-llm",
            "--no-rag",
            "--output-dir", "/tmp/out",
        ])
        assert args.data_source == "databricks"
        assert args.no_llm is True
        assert args.no_rag is True
        assert args.output_dir == "/tmp/out"


# ===========================================================================
# Task 1.2: Mutual exclusion --data-source databricks + --scenario
# ===========================================================================

class TestMutualExclusion:
    """Testes para exclusao mutua entre --data-source databricks e --scenario."""

    @pytest.mark.unit
    def test_databricks_with_scenario_exits(self):
        """--data-source databricks + --scenario produz erro."""
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args(["--data-source", "databricks", "--scenario", "payment-pool-exhaustion"])
        # A validacao acontece em main(), nao no parser
        from main import _validate_args
        with pytest.raises(SystemExit) as exc_info:
            _validate_args(parser, args)
        assert exc_info.value.code == 2

    @pytest.mark.unit
    def test_simulated_with_scenario_is_valid(self):
        """--data-source simulated + --scenario e valido."""
        from main import build_arg_parser, _validate_args
        parser = build_arg_parser()
        args = parser.parse_args(["--data-source", "simulated", "--scenario", "payment-pool-exhaustion"])
        # Nao deve levantar excecao
        _validate_args(parser, args)


# ===========================================================================
# Task 2: _create_databricks_sources() factory
# ===========================================================================

class TestCreateDatabricksSources:
    """Testes para a factory _create_databricks_sources()."""

    @pytest.mark.unit
    def test_success_with_all_env_vars(self, monkeypatch):
        """Com todas as env vars, retorna tupla de 3 sources."""
        monkeypatch.setenv("DATABRICKS_HOST", "https://test.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test_token")
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "abc123")

        # Mock o SDK para nao precisar de conexao real
        with patch("tools.databricks_connector.DatabricksConnection._get_client") as mock_client:
            mock_client.return_value = MagicMock()
            from main import _create_databricks_sources
            result = _create_databricks_sources()

        assert result is not None
        log_source, metrics_source, trace_source = result
        assert log_source is not None
        assert metrics_source is not None
        assert trace_source is not None

    @pytest.mark.unit
    def test_missing_host_returns_none(self, monkeypatch, capsys):
        """Sem DATABRICKS_HOST, retorna None e imprime erro."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test_token")
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "abc123")

        from main import _create_databricks_sources
        result = _create_databricks_sources()

        assert result is None

    @pytest.mark.unit
    def test_missing_token_returns_none(self, monkeypatch):
        """Sem DATABRICKS_TOKEN, retorna None."""
        monkeypatch.setenv("DATABRICKS_HOST", "https://test.databricks.com")
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "abc123")

        from main import _create_databricks_sources
        result = _create_databricks_sources()

        assert result is None

    @pytest.mark.unit
    def test_missing_warehouse_returns_none(self, monkeypatch):
        """Sem DATABRICKS_WAREHOUSE_ID, retorna None."""
        monkeypatch.setenv("DATABRICKS_HOST", "https://test.databricks.com")
        monkeypatch.setenv("DATABRICKS_TOKEN", "dapi_test_token")
        monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)

        from main import _create_databricks_sources
        result = _create_databricks_sources()

        assert result is None

    @pytest.mark.unit
    def test_missing_all_returns_none(self, monkeypatch):
        """Sem nenhuma env var, retorna None."""
        monkeypatch.delenv("DATABRICKS_HOST", raising=False)
        monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
        monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)

        from main import _create_databricks_sources
        result = _create_databricks_sources()

        assert result is None


# ===========================================================================
# Task 3: step_fetch_databricks_data() graceful fallback
# ===========================================================================

class TestStepFetchDatabricksData:
    """Testes para step_fetch_databricks_data() e fallback gracioso."""

    @pytest.mark.unit
    def test_graceful_fallback_on_log_failure(self):
        """Falha em log_source nao crasha — usa dados vazios."""
        from main import step_fetch_databricks_data

        log_source = MagicMock()
        log_source.search.side_effect = Exception("Auth expired")
        metrics_source = MagicMock()
        metrics_source.query.return_value = []
        trace_source = MagicMock()
        trace_source.find_traces.return_value = []

        incident, scenario_name = step_fetch_databricks_data(
            log_source, metrics_source, trace_source,
        )

        assert incident is not None
        assert incident.logs == []
        assert scenario_name == "databricks-live"

    @pytest.mark.unit
    def test_graceful_fallback_on_metrics_failure(self):
        """Falha em metrics_source nao crasha — usa dados vazios."""
        from main import step_fetch_databricks_data

        log_source = MagicMock()
        log_source.search.return_value = []
        metrics_source = MagicMock()
        metrics_source.query.side_effect = Exception("Timeout")
        trace_source = MagicMock()
        trace_source.find_traces.return_value = []

        incident, scenario_name = step_fetch_databricks_data(
            log_source, metrics_source, trace_source,
        )

        assert incident is not None
        assert incident.metrics == []

    @pytest.mark.unit
    def test_graceful_fallback_on_traces_failure(self):
        """Falha em trace_source nao crasha — usa dados vazios."""
        from main import step_fetch_databricks_data

        log_source = MagicMock()
        log_source.search.return_value = []
        metrics_source = MagicMock()
        metrics_source.query.return_value = []
        trace_source = MagicMock()
        trace_source.find_traces.side_effect = Exception("Connection refused")

        incident, scenario_name = step_fetch_databricks_data(
            log_source, metrics_source, trace_source,
        )

        assert incident is not None
        assert incident.traces == []

    @pytest.mark.unit
    def test_all_connectors_fail_still_returns_incident(self):
        """Todos os conectores falham — ainda retorna incidente vazio."""
        from main import step_fetch_databricks_data

        log_source = MagicMock()
        log_source.search.side_effect = Exception("Fail 1")
        metrics_source = MagicMock()
        metrics_source.query.side_effect = Exception("Fail 2")
        trace_source = MagicMock()
        trace_source.find_traces.side_effect = Exception("Fail 3")

        incident, scenario_name = step_fetch_databricks_data(
            log_source, metrics_source, trace_source,
        )

        assert incident is not None
        assert len(incident.logs) == 0
        assert len(incident.metrics) == 0
        assert len(incident.traces) == 0

    @pytest.mark.unit
    def test_successful_data_conversion(self):
        """Dados do conector sao convertidos para dataclasses."""
        from main import step_fetch_databricks_data
        from data.models import LogEntry, MetricPoint, Span

        now_iso = datetime.now().isoformat()

        log_source = MagicMock()
        log_source.search.return_value = [
            {"timestamp": now_iso, "level": "ERROR", "service": "svc-a", "message": "test msg"},
        ]
        metrics_source = MagicMock()
        metrics_source.query.return_value = [
            {"timestamp": now_iso, "service": "svc-a", "metric": "cpu", "value": 85.0},
        ]
        trace_source = MagicMock()
        trace_source.find_traces.return_value = [
            {"trace_id": "t1", "service": "svc-a", "operation": "GET", "duration_ms": 500.0, "status": "OK", "attributes": {}},
        ]

        incident, scenario_name = step_fetch_databricks_data(
            log_source, metrics_source, trace_source,
        )

        assert len(incident.logs) == 1
        assert isinstance(incident.logs[0], LogEntry)
        assert incident.logs[0].service == "svc-a"

        assert len(incident.metrics) == 1
        assert isinstance(incident.metrics[0], MetricPoint)
        assert incident.metrics[0].metric_name == "cpu"

        assert len(incident.traces) == 1
        assert isinstance(incident.traces[0], Span)
        assert incident.traces[0].trace_id == "t1"


# ===========================================================================
# Task 5.7: NFR19 — simulated mode no Databricks imports
# ===========================================================================

class TestNFR19:
    """Testes para preservacao de NFR19."""

    @pytest.mark.unit
    def test_simulated_mode_does_not_import_databricks_connector(self):
        """--data-source simulated NAO importa tools.databricks_connector."""
        # Remove o modulo se ja carregado
        was_loaded = "tools.databricks_connector" in sys.modules
        if was_loaded:
            saved = sys.modules.pop("tools.databricks_connector")

        try:
            # Apenas parsing de args — nao executa main()
            args = _parse_args(["--data-source", "simulated"])
            assert args.data_source == "simulated"
            # Em modo simulated, nenhuma funcao que importa databricks_connector
            # deveria ter sido chamada
            # Verificamos que o modulo NAO foi importado pelo mero parsing
            assert "tools.databricks_connector" not in sys.modules
        finally:
            if was_loaded:
                sys.modules["tools.databricks_connector"] = saved
