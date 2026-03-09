"""
Testes para a ferramenta MCP validate_action (Story 5.3).

Verifica as 4 camadas de segurança, veredicto final e audit trail.
"""

from __future__ import annotations

import json

import pytest


class _MockSanitizer:
    """Sanitizer que não detecta PII."""

    def sanitize_text(self, text):
        return text  # Sem alteração = sem PII


class _MockSanitizerWithPII:
    """Sanitizer que simula detecção de PII."""

    def sanitize_text(self, text):
        return "[REDACTED]"  # Texto diferente = PII detectado


class _MockPreValidator:
    """PreExecutionValidator que aprova ações limpas."""

    def validate(self, action, diagnosis, system_state=None):
        class Result:
            is_safe = True
            risk_score = 10.0
            recommendation = "Passed blast radius and blocklist checks"

        return Result()


class _MockPreValidatorBlocking:
    """PreExecutionValidator que bloqueia comandos rm -rf /."""

    def validate(self, action, diagnosis, system_state=None):
        class Result:
            is_safe = False
            risk_score = 90.0
            recommendation = "⛔ ACTION BLOCKED: matches dangerous pattern"

        return Result()


class _MockTNR:
    """TNR que sempre aprova."""

    def simulate_check(self, action, pre_health, post_health):
        from safety.tnr import TNRReport, TNRResult

        class FakeSnapshot:
            @property
            def health_score(self):
                return 80.0

        return TNRReport(
            result=TNRResult.SUCCESS,
            pre_snapshot=pre_health,
            post_snapshot=post_health,
            action=action,
            health_delta=0.0,
            message="Health snapshot comparison passed",
        )


class _MockVersionValidator:
    """VersionValidator que aprova remediações sem CVE."""

    def validate_remediation(self, command, description=""):
        class Result:
            is_safe = True
            recommendation = "No known CVEs in referenced versions"

        return Result()


class _MockVersionValidatorWithCVE:
    """VersionValidator que detecta CVE."""

    def validate_remediation(self, command, description=""):
        class Result:
            is_safe = False
            recommendation = "⛔ REMEDIATION BLOCKED — CVE-2023-5868 detected"

        return Result()


@pytest.mark.unit
class TestHandleValidateAction:
    """Testes para handle_validate_action()."""

    @pytest.mark.asyncio
    async def test_safe_action_returns_approved(self):
        """Verifica que ação segura retorna approved=True."""
        from mcp_server.tools import handle_validate_action

        result_json = await handle_validate_action(
            arguments={
                "command": "kubectl rollout undo deployment/payment-svc",
                "target_service": "payment-svc",
            },
            sanitizer=_MockSanitizer(),
            pre_validator=_MockPreValidator(),
            tnr=_MockTNR(),
            version_validator=_MockVersionValidator(),
        )
        data = json.loads(result_json)
        assert data["approved"] is True
        assert data["risk_score"] < 0.3
        assert data["blocked_by"] == []

    @pytest.mark.asyncio
    async def test_blocked_command_returns_unapproved(self):
        """Verifica que comando bloqueado retorna approved=False."""
        from mcp_server.tools import handle_validate_action

        result_json = await handle_validate_action(
            arguments={
                "command": "rm -rf /",
                "target_service": "payment-svc",
            },
            sanitizer=_MockSanitizer(),
            pre_validator=_MockPreValidatorBlocking(),
            tnr=_MockTNR(),
            version_validator=_MockVersionValidator(),
        )
        data = json.loads(result_json)
        assert data["approved"] is False
        assert "PreExecutionValidator" in data["blocked_by"]

    @pytest.mark.asyncio
    async def test_pii_in_command_detected(self):
        """Verifica que PII no comando é detectado pela camada 1."""
        from mcp_server.tools import handle_validate_action

        result_json = await handle_validate_action(
            arguments={
                "command": "kubectl exec --user john@example.com",
                "target_service": "auth-svc",
            },
            sanitizer=_MockSanitizerWithPII(),
            pre_validator=_MockPreValidator(),
            tnr=_MockTNR(),
            version_validator=_MockVersionValidator(),
        )
        data = json.loads(result_json)
        assert "TelemetrySanitizer" in data["blocked_by"]

    @pytest.mark.asyncio
    async def test_all_4_layers_execute_even_on_failure(self):
        """Verifica que todas as 4 camadas executam mesmo quando a primeira falha."""
        from mcp_server.tools import handle_validate_action

        result_json = await handle_validate_action(
            arguments={
                "command": "some-command",
                "target_service": "svc",
            },
            sanitizer=_MockSanitizerWithPII(),  # Camada 1 falha
            pre_validator=_MockPreValidatorBlocking(),  # Camada 2 falha
            tnr=_MockTNR(),  # Camada 3 passa
            version_validator=_MockVersionValidatorWithCVE(),  # Camada 4 falha
        )
        data = json.loads(result_json)
        # Todas 4 camadas presentes na resposta
        assert len(data["layers"]) == 4
        layer_names = {lr["name"] for lr in data["layers"]}
        assert "TelemetrySanitizer" in layer_names
        assert "PreExecutionValidator" in layer_names
        assert "TNR" in layer_names
        assert "VersionValidator" in layer_names

    @pytest.mark.asyncio
    async def test_verdict_includes_rationale(self):
        """Verifica que veredicto inclui rationale combinando camadas bloqueantes."""
        from mcp_server.tools import handle_validate_action

        result_json = await handle_validate_action(
            arguments={
                "command": "kubectl apply",
                "target_service": "svc",
            },
            sanitizer=_MockSanitizer(),
            pre_validator=_MockPreValidatorBlocking(),
            tnr=_MockTNR(),
            version_validator=_MockVersionValidatorWithCVE(),
        )
        data = json.loads(result_json)
        assert "rationale" in data
        assert len(data["rationale"]) > 0
        # Rationale menciona camadas bloqueantes
        assert "PreExecutionValidator" in data["rationale"] or "blocked" in data["rationale"].lower()

    @pytest.mark.asyncio
    async def test_empty_command_returns_validation_error(self):
        """Verifica que comando vazio retorna erro RFC 7807."""
        from mcp_server.tools import handle_validate_action

        result_json = await handle_validate_action(
            arguments={"command": "", "target_service": "svc"},
            sanitizer=_MockSanitizer(),
            pre_validator=_MockPreValidator(),
            tnr=_MockTNR(),
            version_validator=_MockVersionValidator(),
        )
        data = json.loads(result_json)
        assert data["type"] == "validation_error"
        assert data["status"] == 400

    @pytest.mark.asyncio
    async def test_empty_target_service_returns_validation_error(self):
        """Verifica que target_service vazio retorna erro RFC 7807."""
        from mcp_server.tools import handle_validate_action

        result_json = await handle_validate_action(
            arguments={"command": "kubectl get pods", "target_service": ""},
            sanitizer=_MockSanitizer(),
            pre_validator=_MockPreValidator(),
            tnr=_MockTNR(),
            version_validator=_MockVersionValidator(),
        )
        data = json.loads(result_json)
        assert data["type"] == "validation_error"

    @pytest.mark.asyncio
    async def test_response_includes_latency(self):
        """Verifica que resposta inclui latency_ms."""
        from mcp_server.tools import handle_validate_action

        result_json = await handle_validate_action(
            arguments={"command": "kubectl get pods", "target_service": "svc"},
            sanitizer=_MockSanitizer(),
            pre_validator=_MockPreValidator(),
            tnr=_MockTNR(),
            version_validator=_MockVersionValidator(),
        )
        data = json.loads(result_json)
        assert "latency_ms" in data
        assert data["latency_ms"] >= 0
