"""MCP `validate_action` não afirma um TNR que não foi medido.

P2-11 fez o SafetyOrchestrator registrar `{"verified": False, ...}` quando não
há saúde pré/pós (o MCP nunca passa `system_state`). O adaptador do MCP
procurava `.message` nesse dict, não achava, e respondia "TNR check passed" e
"Action approved by all 4 safety layers." (achado do pré-PR). Não bloquear é
o projetado; o texto é que tem de dizer "não verificado".
"""
from __future__ import annotations

import asyncio
import json

import pytest

pytestmark = pytest.mark.unit


def _validate() -> dict:
    from interfaces.mcp.tools import handle_validate_action
    from pipeline.telemetry_sanitizer import TelemetrySanitizer
    from safety.pre_validator import PreExecutionValidator
    from safety.tnr import TNRSafetyGuard
    from safety.version_validator import VersionValidator

    out = asyncio.run(handle_validate_action(
        {"command": "kubectl rollout restart deployment/payment-svc",
         "target_service": "payment-svc"},
        TelemetrySanitizer(), PreExecutionValidator(), TNRSafetyGuard(), VersionValidator(),
    ))
    return json.loads(out)


def test_tnr_layer_says_not_verified_with_real_layers():
    data = _validate()
    tnr = next(layer for layer in data["layers"] if layer["name"] == "TNR")
    assert "TNR check passed" not in tnr["detail"]
    assert tnr["detail"].startswith("TNR not verified")
    assert "no post-action health measurement" in tnr["detail"]
    # Projetado (P2-11): não verificado não bloqueia.
    assert tnr["passed"] is True
    assert "TNR" not in data["blocked_by"]


def test_rationale_does_not_claim_all_four_layers_approved():
    data = _validate()
    assert "all 4" not in data["rationale"]
    assert "TNR not verified" in data["rationale"]


@pytest.mark.parametrize(("detail", "expected"), [
    ({"verified": False, "reason": "no post-action health measurement"},
     "TNR not verified: no post-action health measurement"),
    ({"verified": False, "error": "RuntimeError('x')"}, "TNR check failed: RuntimeError('x')"),
    (None, "TNR not verified: no health measurement"),
])
def test_tnr_detail_from_orchestrator_shapes(detail, expected):
    from interfaces.mcp.tools import _tnr_layer_detail

    assert _tnr_layer_detail(detail) == expected


def test_tnr_report_message_is_kept():
    from interfaces.mcp.tools import _tnr_layer_detail

    class Report:
        message = "health improved 40 -> 80"

    assert _tnr_layer_detail(Report()) == "health improved 40 -> 80"
