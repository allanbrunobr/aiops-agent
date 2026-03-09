"""
Testes para saida JSON machine-readable (Story 5.3).

Cobre: serializacao de dataclasses, CLI flag --output-format,
geracao de arquivo JSON valido, edge cases.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime

import pytest


# === Task 1 Tests: CLI --output-format flag ===

@pytest.mark.unit
class TestOutputFormatFlag:
    """Testa o argumento --output-format no CLI."""

    def test_output_format_default_is_markdown(self):
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.output_format == "markdown"

    def test_output_format_json(self):
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args(["--output-format", "json"])
        assert args.output_format == "json"

    def test_output_format_both(self):
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args(["--output-format", "both"])
        assert args.output_format == "both"

    def test_output_format_markdown_explicit(self):
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args(["--output-format", "markdown"])
        assert args.output_format == "markdown"

    def test_output_format_invalid_choice_rejected(self):
        from main import build_arg_parser
        parser = build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--output-format", "xml"])

    def test_output_format_combines_with_other_flags(self):
        from main import build_arg_parser
        parser = build_arg_parser()
        args = parser.parse_args([
            "--output-format", "json",
            "--no-llm",
            "--no-rag",
            "--output-dir", "/tmp/test",
            "--scenario", "memory-leak",
            "--config", "/tmp/aiops.toml",
        ])
        assert args.output_format == "json"
        assert args.no_llm is True
        assert args.no_rag is True
        assert args.output_dir == "/tmp/test"
        assert args.scenario == "memory-leak"
        assert args.config == "/tmp/aiops.toml"


# === Task 2 Tests: _serialize_result() and _json_default() ===

@pytest.mark.unit
class TestJsonDefault:
    """Testa o encoder fallback para tipos nao-serializaveis."""

    def test_datetime_serializes_to_iso(self):
        from main import _json_default
        dt = datetime(2026, 3, 7, 14, 30, 0)
        assert _json_default(dt) == "2026-03-07T14:30:00"

    def test_enum_serializes_to_value(self):
        from main import _json_default
        from safety.tnr import TNRResult
        assert _json_default(TNRResult.SUCCESS) == "success"
        assert _json_default(TNRResult.REGRESSION) == "regression"

    def test_dataclass_serializes_to_dict(self):
        from main import _json_default
        from safety.tnr import MitigationAction
        action = MitigationAction(
            description="test",
            command="echo test",
            risk_level="LOW",
        )
        result = _json_default(action)
        assert isinstance(result, dict)
        assert result["description"] == "test"

    def test_unknown_type_serializes_to_str(self):
        from main import _json_default

        class CustomObj:
            def __str__(self):
                return "custom_repr"

        assert _json_default(CustomObj()) == "custom_repr"


@pytest.mark.unit
class TestSerializeResult:
    """Testa _serialize_result() produz dict JSON-safe completo."""

    def _make_minimal_result(self):
        """Cria result dict minimo para testes."""
        return {
            "stages": {
                "detection": {
                    "is_incident": True,
                    "severity": "HIGH",
                    "signals": ["signal1", "signal2"],
                },
                "diagnosis": {
                    "root_cause": "Test root cause",
                    "confidence": "high",
                    "evidence": ["ev1", "ev2"],
                    "timeline": ["t1"],
                },
            },
            "scenario_name": "test-scenario",
            "config_source": "defaults",
            "success": True,
            "llm_model": "test-model",
            "timeline": [
                {
                    "time": "14:00:00",
                    "from": "IDLE",
                    "to": "DETECTING",
                    "reason": "start",
                }
            ],
        }

    def _make_minimal_incident(self):
        """Cria incident minimo para testes."""
        from data.models import SimulatedIncident
        now = datetime(2026, 3, 7, 14, 0, 0)
        return SimulatedIncident(
            logs=[],
            metrics=[],
            traces=[],
            deploy_time=now,
            spike_start=now,
            detection_time=now,
            fix_time=now,
            actual_root_cause="test cause",
            actual_fix="test fix",
        )

    def test_produces_valid_json(self):
        from main import _serialize_result
        result = self._make_minimal_result()
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        # Deve ser serializavel com json.dumps sem erro
        json_str = json.dumps(serialized, default=lambda o: str(o))
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

    def test_contains_meta_section(self):
        from main import _serialize_result
        result = self._make_minimal_result()
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        assert "meta" in serialized
        assert serialized["meta"]["scenario"] == "test-scenario"
        assert serialized["meta"]["llm_model"] == "test-model"

    def test_contains_detection_section(self):
        from main import _serialize_result
        result = self._make_minimal_result()
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        assert "detection" in serialized
        assert serialized["detection"]["is_incident"] is True

    def test_contains_diagnosis_section(self):
        from main import _serialize_result
        result = self._make_minimal_result()
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        assert "diagnosis" in serialized
        assert serialized["diagnosis"]["root_cause"] == "Test root cause"
        assert serialized["diagnosis"]["confidence"] == "high"

    def test_contains_timeline_section(self):
        from main import _serialize_result
        result = self._make_minimal_result()
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        assert "timeline" in serialized
        assert "incident_timeline" in serialized["timeline"]
        assert "state_transitions" in serialized["timeline"]

    def test_mitigation_action_serialized(self):
        from main import _serialize_result
        from safety.tnr import MitigationAction
        result = self._make_minimal_result()
        result["stages"]["mitigation"] = {
            "action": MitigationAction(
                description="Rollback config",
                command="kubectl rollout undo",
                risk_level="LOW",
                rollback_command="kubectl apply",
            ),
            "pattern_matched": "connection pool",
        }
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        assert "mitigation" in serialized
        mit = serialized["mitigation"]
        assert mit["action"]["description"] == "Rollback config"
        assert mit["action"]["command"] == "kubectl rollout undo"
        assert mit["action"]["risk_level"] == "LOW"
        assert mit["action"]["rollback_command"] == "kubectl apply"

    def test_health_snapshot_includes_health_score(self):
        from main import _serialize_result
        from safety.tnr import HealthSnapshot
        result = self._make_minimal_result()
        now = datetime(2026, 3, 7, 14, 0, 0)
        pre = HealthSnapshot(
            timestamp=now,
            error_rate=0.1,
            latency_p99_ms=120.0,
            availability=99.9,
            connection_pool_active=50,
            connection_pool_max=200,
        )
        result["stages"]["verification"] = {
            "result": "success",
            "health_delta": 75.0,
            "message": "Improvement confirmed",
            "pre_snapshot": pre,
            "post_snapshot": pre,
        }
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        verification = serialized["verification"]
        assert "pre_snapshot" in verification
        # health_score is a @property — must be injected
        assert "health_score" in verification["pre_snapshot"]
        assert isinstance(verification["pre_snapshot"]["health_score"], float)

    def test_threshold_config_serialized(self):
        from main import _serialize_result
        from config.loader import AppConfig
        result = self._make_minimal_result()
        result["threshold_config"] = AppConfig()
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        assert "config" in serialized
        assert serialized["config"]["thresholds"]["sigma_threshold"] == 3.0
        assert serialized["config"]["thresholds"]["baseline_ratio"] == 0.5

    def test_empty_stages_produce_valid_json(self):
        """Edge case: result com stages vazios."""
        from main import _serialize_result
        result = {
            "stages": {},
            "scenario_name": "test",
            "config_source": "defaults",
            "success": False,
            "llm_model": "none",
            "timeline": [],
        }
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        json_str = json.dumps(serialized, default=lambda o: str(o))
        parsed = json.loads(json_str)
        assert parsed["detection"] == {}
        assert parsed["diagnosis"] == {}

    def test_safety_validation_serialized(self):
        from main import _serialize_result
        result = self._make_minimal_result()
        result["stages"]["safety_validation"] = {
            "risk_level": "LOW",
            "risk_score": 15,
            "approval_status": "auto_approved",
            "requires_human": False,
            "blast_radius_scope": "single_service",
            "recommendation": "Safe to proceed",
        }
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        assert "safety_validation" in serialized
        assert serialized["safety_validation"]["risk_level"] == "LOW"

    def test_remediation_validation_serialized(self):
        from main import _serialize_result
        result = self._make_minimal_result()
        result["stages"]["remediation_validation"] = {
            "risk": "NONE",
            "is_safe": True,
            "blocked_patterns": [],
            "warnings": [],
            "recommendation": "All clear",
        }
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        assert "remediation_validation" in serialized
        assert serialized["remediation_validation"]["is_safe"] is True

    def test_action_items_included(self):
        from main import _serialize_result
        result = self._make_minimal_result()
        result["stages"]["mitigation"] = {
            "pattern_matched": "connection pool",
        }
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        assert "action_items" in serialized
        assert len(serialized["action_items"]) > 0

    def test_full_roundtrip_json(self):
        """Testa que o resultado completo sobrevive serialize -> dump -> load."""
        from main import _serialize_result, _json_default
        from safety.tnr import MitigationAction, HealthSnapshot
        from config.loader import AppConfig

        now = datetime(2026, 3, 7, 14, 0, 0)
        result = self._make_minimal_result()
        result["stages"]["mitigation"] = {
            "action": MitigationAction(
                description="test",
                command="echo",
                risk_level="LOW",
            ),
            "pattern_matched": "default",
        }
        result["stages"]["verification"] = {
            "result": "success",
            "health_delta": 50.0,
            "message": "OK",
            "pre_snapshot": HealthSnapshot(
                timestamp=now,
                error_rate=0.1,
                latency_p99_ms=100.0,
                availability=99.9,
                connection_pool_active=10,
                connection_pool_max=200,
            ),
            "post_snapshot": HealthSnapshot(
                timestamp=now,
                error_rate=0.01,
                latency_p99_ms=50.0,
                availability=99.99,
                connection_pool_active=5,
                connection_pool_max=200,
            ),
        }
        result["threshold_config"] = AppConfig()
        incident = self._make_minimal_incident()
        serialized = _serialize_result(result, incident)
        json_str = json.dumps(serialized, default=_json_default, indent=2)
        parsed = json.loads(json_str)
        assert parsed["meta"]["scenario"] == "test-scenario"
        assert parsed["diagnosis"]["root_cause"] == "Test root cause"
        assert parsed["mitigation"]["action"]["command"] == "echo"
        assert "health_score" in parsed["verification"]["pre_snapshot"]
