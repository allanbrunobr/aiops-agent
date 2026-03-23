"""
Testes unitários e de integração para o cenário unknown-fault (Story 2.4).

Valida:
- Registro do cenário no registry
- Determinismo com seed=42
- _heuristic_diagnosis() retorna confidence="low" para fault desconhecido
- MitigationAgent.propose() seleciona padrão "unknown" para baixa confiança
- Pipeline completo gera postmortem válido
- Snapshots de diagnóstico e mitigação do cenário

Use --snapshot-update para regenerar snapshots após mudanças intencionais.
"""
from __future__ import annotations

import datetime
import io
from contextlib import redirect_stdout, redirect_stderr

import pytest

# Garante registro de todos os cenários no registry antes dos testes.
import data.sample_incident  # noqa: F401

from data.models import LogEntry, MetricPoint, Span, SimulatedIncident
from data.registry import get_scenario, list_scenarios


@pytest.mark.unit
class TestUnknownFaultRegistry:
    """Testes do registry para cenário unknown-fault (AC: #1)."""

    def test_unknown_fault_registered(self) -> None:
        """Cenário 'unknown-fault' deve estar registrado no registry."""
        scenarios = list_scenarios()
        assert "unknown-fault" in scenarios

    def test_unknown_fault_cli_selectable(self) -> None:
        """--scenario unknown-fault deve ser aceito pelo argparse."""
        from main import build_arg_parser

        parser = build_arg_parser()
        args = parser.parse_args(["--scenario", "unknown-fault"])
        assert args.scenario == "unknown-fault"


@pytest.mark.unit
class TestUnknownFaultDeterminism:
    """Testes de determinismo por seed (AC: #1)."""

    def test_scenario_deterministic_seed42(self) -> None:
        """Duas chamadas com seed=42 devem produzir output idêntico."""
        fn = get_scenario("unknown-fault")
        inc1 = fn(seed=42)
        inc2 = fn(seed=42)

        assert len(inc1.logs) == len(inc2.logs)
        assert len(inc1.metrics) == len(inc2.metrics)
        assert len(inc1.traces) == len(inc2.traces)
        assert inc1.actual_root_cause == inc2.actual_root_cause
        assert inc1.actual_fix == inc2.actual_fix

        for log1, log2 in zip(inc1.logs, inc2.logs):
            assert str(log1) == str(log2)

        for m1, m2 in zip(inc1.metrics, inc2.metrics):
            assert m1.service == m2.service
            assert m1.metric_name == m2.metric_name
            assert m1.value == m2.value

        for t1, t2 in zip(inc1.traces, inc2.traces):
            assert t1.trace_id == t2.trace_id
            assert t1.span_id == t2.span_id
            assert t1.service == t2.service
            assert t1.duration_ms == t2.duration_ms
            assert t1.status == t2.status


@pytest.mark.unit
class TestUnknownFaultContract:
    """Testes do contrato SimulatedIncident para unknown-fault."""

    def test_unknown_fault_has_correct_services(self) -> None:
        """Cenário unknown-fault deve ter telemetria de pelo menos 3 serviços distintos."""
        fn = get_scenario("unknown-fault")
        inc = fn(seed=42)
        services = set(log.service for log in inc.logs)
        assert len(services) >= 3, f"Esperado >=3 serviços, encontrado {len(services)}: {services}"

    def test_unknown_fault_root_cause_is_non_standard(self) -> None:
        """actual_root_cause NÃO deve conter keywords de padrões conhecidos."""
        fn = get_scenario("unknown-fault")
        inc = fn(seed=42)
        rc_lower = inc.actual_root_cause.lower()
        # Deve ser um fault type que NÃO corresponde a padrões heurísticos.
        assert "connection pool" not in rc_lower
        assert "pool exhaustion" not in rc_lower
        assert "timeout" not in rc_lower

    def test_unknown_fault_contract(self) -> None:
        """SimulatedIncident do unknown-fault deve ter todos os 9 campos com tipos corretos."""
        fn = get_scenario("unknown-fault")
        inc = fn(seed=42)

        expected_fields = {
            "logs", "metrics", "traces",
            "deploy_time", "spike_start", "detection_time", "fix_time",
            "actual_root_cause", "actual_fix",
        }
        actual_fields = set(inc.__dataclass_fields__.keys())
        assert actual_fields == expected_fields

        assert isinstance(inc, SimulatedIncident)
        assert isinstance(inc.logs, list) and len(inc.logs) > 0
        assert isinstance(inc.metrics, list) and len(inc.metrics) > 0
        assert isinstance(inc.traces, list) and len(inc.traces) > 0
        assert isinstance(inc.logs[0], LogEntry)
        assert isinstance(inc.metrics[0], MetricPoint)
        assert isinstance(inc.traces[0], Span)
        assert isinstance(inc.deploy_time, datetime.datetime)
        assert isinstance(inc.actual_root_cause, str) and len(inc.actual_root_cause) > 0
        assert isinstance(inc.actual_fix, str) and len(inc.actual_fix) > 0

    def test_unknown_fault_logs_no_heuristic_keywords(self) -> None:
        """Logs do cenário NÃO devem conter keywords dos padrões heurísticos.

        _heuristic_diagnosis() escaneia TODOS os Drain3 templates (qualquer nível,
        qualquer serviço). Verificamos todos os logs — não apenas ERROR do serviço
        primário — para garantir que nenhum template gerado pelo Drain3 contenha
        keywords que ativariam known_pattern_matched=True, forçando confidence="low".
        """
        fn = get_scenario("unknown-fault")
        inc = fn(seed=42)

        heuristic_keywords = [
            "connection pool", "pool exhausted",
            "memory", "oom", "heap",
            "timed out", "timeout",
        ]
        for log in inc.logs:
            msg_lower = log.message.lower()
            for kw in heuristic_keywords:
                assert kw not in msg_lower, (
                    f"Log [{log.level}] de {log.service} contém keyword heurística "
                    f"'{kw}': {log.message}"
                )


@pytest.mark.unit
class TestHeuristicDiagnosisConfidence:
    """Testes de _heuristic_diagnosis() com diferentes cenários (AC: #2)."""

    def _run_heuristic(self, scenario_name: str) -> dict:
        """Helper: executa pipeline até diagnóstico heurístico e retorna dict."""
        from data.registry import get_scenario
        from pipeline.telemetry_sanitizer import TelemetrySanitizer
        from pipeline.aggregator import Aggregator
        from agents.diagnoser import DiagnosisAgent

        incident = get_scenario(scenario_name)(seed=42)

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            sanitizer = TelemetrySanitizer(strict_mode=False)
            s_logs, s_metrics, s_traces, _ = sanitizer.sanitize_all(
                incident.logs, incident.metrics, incident.traces,
            )
            incident.logs = s_logs
            incident.metrics = s_metrics
            incident.traces = s_traces

            aggregator = Aggregator()
            report = aggregator.process(incident.logs, incident.metrics, incident.traces)

            diagnoser = DiagnosisAgent(max_iterations=1)
            return diagnoser._heuristic_diagnosis(report)

    def test_unknown_fault_returns_low_confidence(self) -> None:
        """unknown-fault deve produzir confidence='low' na heurística."""
        diagnosis = self._run_heuristic("unknown-fault")
        assert diagnosis["confidence"] == "low", (
            f"Esperado confidence='low', obtido '{diagnosis['confidence']}'"
        )

    def test_unknown_fault_root_cause_mentions_unknown(self) -> None:
        """unknown-fault deve produzir root_cause não-vazio e descritivo."""
        diagnosis = self._run_heuristic("unknown-fault")
        assert diagnosis["root_cause"], "root_cause deve ser não-vazio"
        assert len(diagnosis["root_cause"]) > 10, (
            f"root_cause deve ser descritivo, obtido: '{diagnosis['root_cause']}'"
        )

    def test_payment_pool_returns_medium_confidence(self) -> None:
        """payment-pool-exhaustion deve continuar retornando confidence='medium'."""
        diagnosis = self._run_heuristic("payment-pool-exhaustion")
        assert diagnosis["confidence"] == "medium", (
            f"Esperado confidence='medium', obtido '{diagnosis['confidence']}'"
        )

    def test_memory_leak_returns_medium_confidence(self) -> None:
        """memory-leak deve continuar retornando confidence='medium'."""
        diagnosis = self._run_heuristic("memory-leak")
        assert diagnosis["confidence"] == "medium", (
            f"Esperado confidence='medium', obtido '{diagnosis['confidence']}'"
        )

    def test_diagnosis_dict_shape_preserved(self) -> None:
        """Diagnóstico deve conter as chaves do contrato independente do cenário."""
        expected_keys = {"root_cause", "evidence", "affected_services", "confidence", "timeline"}
        for scenario in ["payment-pool-exhaustion", "memory-leak", "unknown-fault"]:
            diagnosis = self._run_heuristic(scenario)
            assert expected_keys <= set(diagnosis.keys()), (
                f"Cenário {scenario}: chaves {set(diagnosis.keys())} não contém {expected_keys}"
            )


@pytest.mark.unit
class TestMitigatorUnknownPattern:
    """Testes do MitigationAgent com padrão 'unknown' (AC: #3)."""

    def test_unknown_pattern_exists(self) -> None:
        """REMEDIATION_PATTERNS deve conter entrada 'unknown'."""
        from agents.mitigator import REMEDIATION_PATTERNS
        assert "unknown" in REMEDIATION_PATTERNS

    def test_unknown_pattern_has_required_keys(self) -> None:
        """Entrada 'unknown' deve ter action, command, risk e rollback."""
        from agents.mitigator import REMEDIATION_PATTERNS
        pattern = REMEDIATION_PATTERNS["unknown"]
        assert "action" in pattern
        assert "command" in pattern
        assert "risk" in pattern
        assert "rollback" in pattern

    def test_unknown_pattern_is_low_risk(self) -> None:
        """Padrão 'unknown' deve ser LOW risk (diagnóstico, não ação destrutiva)."""
        from agents.mitigator import REMEDIATION_PATTERNS
        assert REMEDIATION_PATTERNS["unknown"]["risk"] == "LOW"

    def test_propose_uses_unknown_for_low_confidence(self) -> None:
        """propose() deve selecionar 'unknown' quando confidence='low' e nenhum padrão matched."""
        from agents.mitigator import MitigationAgent

        diagnosis = {
            "root_cause": "Unknown fault type detected in data-processor",
            "evidence": ["Metric anomaly: data-processor/error_rate = 40 (5sigma)"],
            "affected_services": ["data-processor"],
            "confidence": "low",
            "timeline": "Heuristic analysis",
        }

        mitigator = MitigationAgent()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = mitigator.propose(diagnosis)

        assert result["pattern_matched"] == "unknown", (
            f"Esperado pattern_matched='unknown', obtido '{result['pattern_matched']}'"
        )

    def test_propose_uses_default_for_medium_confidence_unknown_cause(self) -> None:
        """propose() com confidence='medium' mas sem padrão match deve usar 'default', não 'unknown'."""
        from agents.mitigator import MitigationAgent

        diagnosis = {
            "root_cause": "Multiple errors detected in some-service",
            "evidence": [],
            "affected_services": ["some-service"],
            "confidence": "medium",
            "timeline": "Heuristic analysis",
        }

        mitigator = MitigationAgent()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = mitigator.propose(diagnosis)

        assert result["pattern_matched"] == "default", (
            f"Esperado pattern_matched='default', obtido '{result['pattern_matched']}'"
        )

    def test_propose_known_pattern_not_affected(self) -> None:
        """propose() com padrão conhecido (e.g., 'connection pool') não deve ser afetado."""
        from agents.mitigator import MitigationAgent

        diagnosis = {
            "root_cause": "Connection pool exhaustion in payment-svc",
            "evidence": [],
            "affected_services": ["payment-svc"],
            "confidence": "medium",
            "timeline": "Heuristic analysis",
        }

        mitigator = MitigationAgent()
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            result = mitigator.propose(diagnosis)

        assert result["pattern_matched"] == "connection pool", (
            f"Esperado 'connection pool', obtido '{result['pattern_matched']}'"
        )


@pytest.mark.integration
class TestUnknownFaultPipeline:
    """Testes de integração do pipeline completo com unknown-fault (AC: #4, #5)."""

    def test_pipeline_completes_successfully(self, unknown_fault_pipeline_result) -> None:
        """Pipeline completo deve concluir sem erros para unknown-fault."""
        result = unknown_fault_pipeline_result
        assert result.get("success") is True, f"Pipeline falhou: {result}"

    def test_pipeline_diagnosis_is_low_confidence(self, unknown_fault_pipeline_result) -> None:
        """Diagnóstico do pipeline unknown-fault deve ter confidence='low'."""
        diagnosis = unknown_fault_pipeline_result["stages"]["diagnosis"]
        assert diagnosis["confidence"] == "low"

    def test_pipeline_mitigation_pattern_is_unknown(self, unknown_fault_pipeline_result) -> None:
        """Mitigação do pipeline unknown-fault deve usar padrão 'unknown'."""
        mitigation = unknown_fault_pipeline_result["stages"]["mitigation"]
        assert mitigation["pattern_matched"] == "unknown"

    def test_pipeline_state_transitions_normal(self, unknown_fault_pipeline_result) -> None:
        """State machine deve seguir transições normais (AC: #4)."""
        timeline = unknown_fault_pipeline_result.get("timeline", [])
        states_visited = [t["to"] for t in timeline]
        # Deve passar por todos os estados normais.
        assert "DETECTING" in states_visited
        assert "DIAGNOSING" in states_visited
        assert "MITIGATING" in states_visited
        assert "VERIFYING" in states_visited
        assert "RESOLVED" in states_visited

    def test_pipeline_has_safety_validation(self, unknown_fault_pipeline_result) -> None:
        """Pipeline deve incluir safety validation mesmo para unknown-fault."""
        stages = unknown_fault_pipeline_result["stages"]
        assert "safety_validation" in stages
        assert "remediation_validation" in stages


@pytest.mark.snapshot
@pytest.mark.integration
class TestUnknownFaultPipelineSnapshots:
    """Snapshots do pipeline unknown-fault (agentes + safety).

    Usa unknown_fault_pipeline_result fixture.
    Verifica que diagnóstico e mitigação são determinísticos para o cenário.
    """

    def test_unknown_fault_diagnosis_snapshot(
        self, unknown_fault_pipeline_result, snapshot_manager,
    ) -> None:
        """Verifica que diagnóstico heurístico do unknown-fault é determinístico."""
        diagnosis = unknown_fault_pipeline_result["stages"]["diagnosis"]
        snapshot_manager.assert_match(diagnosis, "unknown_fault_diagnosis")

    def test_unknown_fault_mitigation_snapshot(
        self, unknown_fault_pipeline_result, snapshot_manager,
    ) -> None:
        """Verifica que ação de mitigação proposta para unknown-fault é determinística."""
        mitigation = unknown_fault_pipeline_result["stages"]["mitigation"]
        snapshot_manager.assert_match(mitigation, "unknown_fault_mitigation")

    def test_unknown_fault_postmortem_structure_snapshot(
        self, unknown_fault_pipeline_result, unknown_fault_incident, snapshot_manager,
    ) -> None:
        """Verifica que estrutura do postmortem para unknown-fault é determinística (AC #3).

        Gera postmortem markdown via step_postmortem(result, orchestrator, incident).
        Timestamps dinâmicos (datetime.now()) são normalizados antes da comparação.
        """
        from main import step_postmortem

        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            postmortem_path, _ = step_postmortem(
                unknown_fault_pipeline_result, unknown_fault_incident,
            )

        postmortem_text = postmortem_path.read_text(encoding="utf-8")
        postmortem_path.unlink(missing_ok=True)

        snapshot_manager.assert_text_match(postmortem_text, "unknown_fault_postmortem_structure")
