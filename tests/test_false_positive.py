from __future__ import annotations

import sys
import types
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


def _inject_server_app(knowledge_store_value: object) -> None:
    """Injeta módulo server.app fake em sys.modules para isolar testes."""
    fake_app = types.ModuleType("server.app")
    fake_app.knowledge_store = knowledge_store_value  # type: ignore[attr-defined]
    sys.modules["server.app"] = fake_app


def _cleanup_server_app() -> None:
    """Remove módulo fake de sys.modules."""
    sys.modules.pop("server.app", None)


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
        _inject_server_app(bad_ks)

        try:
            from server.handlers.false_positive import handle_false_positive_side_effects

            with patch("server.handlers.false_positive.log") as mock_log:
                await handle_false_positive_side_effects(
                    correlation_id="test-123",
                    notes="teste",
                    incident=incident,
                )

                mock_log.warning.assert_called_once_with(
                    "false_positive_side_effect_error",
                    exc_info=True,
                )
        finally:
            _cleanup_server_app()

    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self) -> None:
        """Garante que exceção no bloco try não propaga para o chamador."""
        incident = _make_incident()

        bad_ks = MagicMock()
        bad_ks.get_all_patterns.side_effect = RuntimeError("crash")
        _inject_server_app(bad_ks)

        try:
            from server.handlers.false_positive import handle_false_positive_side_effects

            with patch("server.handlers.false_positive.log"):
                # Não deve lançar exceção
                await handle_false_positive_side_effects(
                    correlation_id="test-456",
                    notes="defensive test",
                    incident=incident,
                )
        finally:
            _cleanup_server_app()

    @pytest.mark.asyncio
    async def test_no_warning_when_no_exception(self) -> None:
        """Garante que log.warning NÃO é emitido quando knowledge_store é None."""
        incident = _make_incident()

        # knowledge_store=None — bloco if _ks is not None é ignorado, sem exceção
        _inject_server_app(None)

        try:
            from server.handlers.false_positive import handle_false_positive_side_effects

            with patch("server.handlers.false_positive.log") as mock_log:
                await handle_false_positive_side_effects(
                    correlation_id="test-789",
                    notes="no error test",
                    incident=incident,
                )

                mock_log.warning.assert_not_called()
        finally:
            _cleanup_server_app()
