from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from domain.incident_state import IncidentState


def _make_incident() -> IncidentState:
    """Cria IncidentState mínimo para testes."""
    incident = MagicMock(spec=IncidentState)
    incident.diagnosis = {"root_cause": "high_cpu"}
    incident.alert = MagicMock()
    incident.alert.affected_services = ["order-svc"]
    return incident


@pytest.mark.unit
class TestHandleFalsePositiveSideEffects:
    """Testes unitários para handle_false_positive_side_effects."""

    @pytest.mark.asyncio
    async def test_warning_logged_when_knowledge_store_raises(self) -> None:
        """Garante que log.warning é emitido quando _mark_knowledge_pattern_negative lança exceção."""
        incident = _make_incident()

        # knowledge_store que lança exceção ao chamar get_all_patterns()
        bad_ks = MagicMock()
        bad_ks.get_all_patterns.side_effect = RuntimeError("db unavailable")

        try:
            from interfaces.http.handlers.false_positive import handle_false_positive_side_effects

            with patch("interfaces.http.handlers.false_positive.log") as mock_log:
                await handle_false_positive_side_effects(
                    correlation_id="test-123",
                    notes="teste",
                    incident=incident,
                    knowledge_store=bad_ks,
                )

                mock_log.warning.assert_called_once_with(
                    "false_positive_side_effect_error",
                    exc_info=True,
                )
        finally:
            pass  # a store é passada explicitamente; nada global a restaurar

    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self) -> None:
        """Garante que exceção no bloco try não propaga para o chamador."""
        incident = _make_incident()

        bad_ks = MagicMock()
        bad_ks.get_all_patterns.side_effect = RuntimeError("crash")

        try:
            from interfaces.http.handlers.false_positive import handle_false_positive_side_effects

            with patch("interfaces.http.handlers.false_positive.log"):
                # Não deve lançar exceção
                await handle_false_positive_side_effects(
                    correlation_id="test-456",
                    notes="defensive test",
                    incident=incident,
                    knowledge_store=bad_ks,
                )
        finally:
            pass  # a store é passada explicitamente; nada global a restaurar

    @pytest.mark.asyncio
    async def test_missing_store_is_logged_not_silent(self) -> None:
        """Sem knowledge_store, o descarte do feedback é registrado (antes era silencioso)."""
        incident = _make_incident()

        # knowledge_store=None — bloco if _ks is not None é ignorado, sem exceção

        try:
            from interfaces.http.handlers.false_positive import handle_false_positive_side_effects

            with patch("interfaces.http.handlers.false_positive.log") as mock_log:
                await handle_false_positive_side_effects(
                    correlation_id="test-789",
                    notes="no error test",
                    incident=incident,
                    knowledge_store=None,
                )

                mock_log.warning.assert_called_once_with(
                    "false_positive_no_knowledge_store", correlation_id="test-789",
                )
        finally:
            pass  # a store é passada explicitamente; nada global a restaurar
