"""Testes unitários para métodos _tool_* do DiagnosisAgent (FR5).

Cobre os três métodos TAMO-inspired:
- _tool_correlate_signals
- _tool_reconstruct_timeline
- _tool_classify_fault_type

Cada método tem pelo menos 2 casos: resposta válida e resposta com args ausentes/malformados.
Não requer ANTHROPIC_API_KEY — os métodos operam sobre AggregatedReport diretamente.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# _tool_correlate_signals
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestToolCorrelateSignals:
    """Testes para _tool_correlate_signals."""

    def test_valid_service_with_matching_data(
        self, payload_correlate_signals, mock_aggregated_report_with_data
    ) -> None:
        """Serviço com dados retorna string de correlação com header esperado."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        result = agent._tool_correlate_signals(
            payload_correlate_signals, mock_aggregated_report_with_data
        )
        assert isinstance(result, str)
        assert "Signal Correlation" in result
        assert "payment-svc" in result

    def test_service_not_found_returns_no_signals_message(
        self, mock_aggregated_report_with_data
    ) -> None:
        """Serviço sem dados retorna mensagem 'No signals found'."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        result = agent._tool_correlate_signals(
            {"service": "nonexistent-svc"}, mock_aggregated_report_with_data
        )
        assert "No signals found" in result

    def test_empty_report_returns_no_signals_message(
        self, payload_correlate_signals, mock_aggregated_report_empty
    ) -> None:
        """Report vazio com serviço válido retorna mensagem 'No signals found'."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        result = agent._tool_correlate_signals(
            payload_correlate_signals, mock_aggregated_report_empty
        )
        assert "No signals found" in result

    def test_missing_service_key_uses_empty_string(
        self, mock_aggregated_report_with_data
    ) -> None:
        """Payload sem chave 'service' usa string vazia — não levanta exceção."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        result = agent._tool_correlate_signals({}, mock_aggregated_report_with_data)
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# _tool_reconstruct_timeline
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestToolReconstructTimeline:
    """Testes para _tool_reconstruct_timeline."""

    def test_empty_services_includes_all_events(
        self, mock_aggregated_report_with_data
    ) -> None:
        """services=[] inclui todos os eventos — retorna header de timeline."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        result = agent._tool_reconstruct_timeline(
            {"services": []}, mock_aggregated_report_with_data
        )
        assert isinstance(result, str)
        assert "Event Timeline" in result

    def test_empty_report_returns_no_events_message(
        self, mock_aggregated_report_empty
    ) -> None:
        """Report sem eventos retorna 'No events found'."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        result = agent._tool_reconstruct_timeline({}, mock_aggregated_report_empty)
        assert "No events found" in result

    def test_filter_by_service_returns_matching_events(
        self, payload_reconstruct_timeline, mock_aggregated_report_with_data
    ) -> None:
        """Filtrar por 'payment-svc' retorna timeline com esse serviço."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        result = agent._tool_reconstruct_timeline(
            payload_reconstruct_timeline, mock_aggregated_report_with_data
        )
        assert isinstance(result, str)
        assert "Event Timeline" in result

    def test_filter_by_unknown_service_returns_no_events(
        self, mock_aggregated_report_with_data
    ) -> None:
        """Filtrar por serviço inexistente retorna 'No events found'."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        result = agent._tool_reconstruct_timeline(
            {"services": ["nonexistent-svc"]}, mock_aggregated_report_with_data
        )
        assert "No events found" in result


# ---------------------------------------------------------------------------
# _tool_classify_fault_type
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestToolClassifyFaultType:
    """Testes para _tool_classify_fault_type."""

    def test_valid_service_returns_classification_header(
        self, payload_classify_fault_type, mock_aggregated_report_with_data
    ) -> None:
        """Serviço com dados retorna string de classificação com header esperado."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        result = agent._tool_classify_fault_type(
            payload_classify_fault_type, mock_aggregated_report_with_data
        )
        assert isinstance(result, str)
        assert "Fault Type Classification" in result

    def test_resource_exhaustion_detected_from_pool_data(
        self, payload_classify_fault_type, mock_aggregated_report_with_data
    ) -> None:
        """Dados com 'pool' e connection_pool elevam score de resource_exhaustion."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        result = agent._tool_classify_fault_type(
            payload_classify_fault_type, mock_aggregated_report_with_data
        )
        assert "resource_exhaustion" in result.lower()

    def test_missing_service_key_processes_all_services(
        self, mock_aggregated_report_with_data
    ) -> None:
        """Payload sem 'service' usa serviço vazio — processa todos os dados sem exceção."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        result = agent._tool_classify_fault_type({}, mock_aggregated_report_with_data)
        assert isinstance(result, str)
        assert "all services" in result

    def test_empty_report_returns_classification_with_no_scores(
        self, payload_classify_fault_type, mock_aggregated_report_empty
    ) -> None:
        """Report vazio retorna header de classificação sem scores positivos."""
        from agents.diagnoser import DiagnosisAgent

        agent = DiagnosisAgent()
        result = agent._tool_classify_fault_type(
            payload_classify_fault_type, mock_aggregated_report_empty
        )
        assert isinstance(result, str)
        assert "Fault Type Classification" in result
        # Sem dados, nenhum fault_type deve aparecer no output (score=0)
        assert "resource_exhaustion" not in result
