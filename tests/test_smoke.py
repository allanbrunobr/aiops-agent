"""
Smoke tests para o pipeline do AIOps Agent.

Valida que o pipeline roda end-to-end em modo --no-llm --no-rag
sem erros, produz todos os estágios esperados e gera output
determinístico com seed-42.
"""
from __future__ import annotations

import io
import random
import sys
from contextlib import redirect_stdout, redirect_stderr

import pytest


@pytest.mark.integration
class TestPipelineSmoke:
    """Testes de fumaça para validar pipeline completo sem LLM/RAG."""

    def test_pipeline_runs_without_errors(self, pipeline_result):
        """Pipeline completa em modo --no-llm --no-rag sem exceções."""
        assert pipeline_result is not None
        assert isinstance(pipeline_result, dict)
        assert "stages" in pipeline_result

    def test_pipeline_produces_all_stage_keys(self, pipeline_result):
        """result['stages'] contém todas as chaves esperadas do pipeline."""
        stages = pipeline_result["stages"]

        # Chaves produzidas pelo orchestrator
        orchestrator_keys = {"detection", "diagnosis", "mitigation", "verification"}
        for key in orchestrator_keys:
            assert key in stages, f"Chave '{key}' ausente em result['stages']"

        # Chaves produzidas pelos steps de validação pós-agentes
        post_agent_keys = {"safety_validation", "remediation_validation"}
        for key in post_agent_keys:
            assert key in stages, f"Chave '{key}' ausente em result['stages']"

    def test_detection_stage_has_content(self, pipeline_result):
        """Estágio de detecção contém dados válidos."""
        detection = pipeline_result["stages"]["detection"]
        assert detection is not None
        assert isinstance(detection, dict)

    def test_diagnosis_has_root_cause(self, pipeline_result):
        """Diagnóstico heurístico retorna root_cause com formato correto."""
        diagnosis = pipeline_result["stages"]["diagnosis"]
        assert diagnosis is not None
        assert isinstance(diagnosis, dict)
        # Heuristic diagnosis retorna estas chaves
        expected_keys = {"root_cause", "evidence", "affected_services", "confidence", "timeline"}
        for key in expected_keys:
            assert key in diagnosis, f"Chave '{key}' ausente no diagnóstico"

    def test_mitigation_has_action(self, pipeline_result):
        """Mitigação propõe uma ação com dataclass MitigationAction."""
        mitigation = pipeline_result["stages"]["mitigation"]
        assert mitigation is not None
        assert "action" in mitigation
        action = mitigation["action"]
        # MitigationAction é um dataclass — deve ter atributos esperados
        assert hasattr(action, "description")
        assert hasattr(action, "command")
        assert hasattr(action, "risk_level")

    def test_verification_stage_exists(self, pipeline_result):
        """Estágio de verificação (TNR) existe e tem resultado."""
        verification = pipeline_result["stages"]["verification"]
        assert verification is not None
        assert isinstance(verification, dict)
        assert "result" in verification

    def test_safety_validation_present(self, pipeline_result):
        """Validação de segurança pré-execução está presente."""
        safety = pipeline_result["stages"]["safety_validation"]
        assert safety is not None
        assert "is_safe" in safety
        assert "risk_level" in safety

    def test_remediation_validation_present(self, pipeline_result):
        """Validação de versão/CVE da remediação está presente."""
        remediation = pipeline_result["stages"]["remediation_validation"]
        assert remediation is not None
        assert "is_safe" in remediation
        assert "risk" in remediation

    def test_pipeline_has_timeline(self, pipeline_result):
        """Pipeline gera timeline de transições do state machine."""
        assert "timeline" in pipeline_result
        timeline = pipeline_result["timeline"]
        assert isinstance(timeline, list)
        assert len(timeline) > 0


@pytest.mark.integration
class TestPipelineDeterminism:
    """Testes de determinismo com seed-42."""

    def test_pipeline_deterministic_seed42(self):
        """Duas execuções do pipeline completo produzem output idêntico."""
        results = []
        for _ in range(2):
            random.seed(42)
            from data.sample_incident import create_incident
            from pipeline.telemetry_sanitizer import TelemetrySanitizer
            from pipeline.aggregator import Aggregator
            from agents.detector import DetectionAgent
            from agents.diagnoser import DiagnosisAgent
            from agents.mitigator import MitigationAgent
            from safety.tnr import TNRSafetyGuard
            from safety.pre_validator import PreExecutionValidator
            from safety.version_validator import VersionValidator

            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                incident = create_incident()
                sanitizer = TelemetrySanitizer(strict_mode=False)
                s_logs, s_metrics, s_traces, _ = sanitizer.sanitize_all(
                    incident.logs, incident.metrics, incident.traces,
                )
                incident.logs = s_logs
                incident.metrics = s_metrics
                incident.traces = s_traces
                aggregator = Aggregator()
                report = aggregator.process(incident.logs, incident.metrics, incident.traces)

                detector = DetectionAgent()
                diagnoser = DiagnosisAgent(max_iterations=1)
                mitigator = MitigationAgent()
                tnr_guard = TNRSafetyGuard()

                from datetime import datetime as _dt2
                _tl: list[dict] = []

                def _r(frm: str, to: str, reason: str) -> None:
                    _tl.append({"time": _dt2.now().strftime("%H:%M:%S"),
                                "from": frm, "to": to, "reason": reason})

                result: dict = {"success": False, "stages": {}}
                _r("IDLE", "DETECTING", "Analisando dados de observabilidade...")
                detection = detector.detect(report)
                result["stages"]["detection"] = detection
                if detection.get("is_incident"):
                    affected = ", ".join(detection.get("affected_services", []))
                    _r("DETECTING", "DIAGNOSING", f"Investigando incidente em {affected}")
                    diagnosis = diagnoser.diagnose(report, None)
                    result["stages"]["diagnosis"] = diagnosis
                    if diagnosis.get("root_cause"):
                        _r("DIAGNOSING", "MITIGATING", "Propondo remediação...")
                        mitigation_r = mitigator.propose(diagnosis)
                        result["stages"]["mitigation"] = mitigation_r
                        _r("MITIGATING", "VERIFYING", "Verificando impacto (TNR)...")
                        tnr_check = mitigator.verify_with_tnr(mitigation_r, tnr_guard)
                        result["stages"]["verification"] = {
                            "result": tnr_check.result.value,
                            "health_delta": tnr_check.health_delta,
                            "message": tnr_check.message,
                        }
                        if tnr_check.result.value != "regression":
                            _r("VERIFYING", "RESOLVED", f"Incidente resolvido: {tnr_check.message}")
                            result["success"] = True
                        else:
                            _r("VERIFYING", "ROLLBACK", f"Regressão detectada: {tnr_check.message}")
                    else:
                        _r("DIAGNOSING", "FAILED", "Não foi possível identificar root cause")
                else:
                    _r("DETECTING", "IDLE", "Nenhum incidente detectado")
                result["timeline"] = _tl

                # Validação de segurança pré-execução
                mitigation = result.get("stages", {}).get("mitigation", {})
                if mitigation and mitigation.get("action"):
                    diagnosis = result.get("stages", {}).get("diagnosis", {})
                    validator = PreExecutionValidator(
                        auto_approve_low_risk=True,
                        max_blast_radius=3,
                        require_rollback_plan=True,
                    )
                    safety_result = validator.validate(mitigation["action"], diagnosis)
                    result["stages"]["safety_validation"] = {
                        "is_safe": safety_result.is_safe,
                        "risk_level": safety_result.risk_level.value,
                        "risk_score": safety_result.risk_score,
                        "approval_status": safety_result.approval_status.value,
                        "requires_human": safety_result.requires_human,
                    }

                # Validação de versão/CVE na remediação
                if mitigation and mitigation.get("action"):
                    action = mitigation["action"]
                    version_validator = VersionValidator()
                    validation = version_validator.validate_mitigation_action(action)
                    result["stages"]["remediation_validation"] = {
                        "is_safe": validation.is_safe,
                        "risk": validation.risk.name,
                        "blocked_patterns": validation.blocked_patterns,
                        "warnings": validation.warnings,
                        "recommendation": validation.recommendation,
                    }

            results.append(result)

        r1, r2 = results

        # Diagnósticos devem ser idênticos
        d1 = r1["stages"]["diagnosis"]
        d2 = r2["stages"]["diagnosis"]
        assert d1["root_cause"] == d2["root_cause"]
        assert d1["confidence"] == d2["confidence"]
        assert d1["affected_services"] == d2["affected_services"]

        # Detecções devem ser idênticas
        det1 = r1["stages"]["detection"]
        det2 = r2["stages"]["detection"]
        assert det1 == det2

        # Validações de segurança devem ser idênticas
        sv1 = r1["stages"].get("safety_validation")
        sv2 = r2["stages"].get("safety_validation")
        assert sv1 == sv2

        # Validações de remediação devem ser idênticas
        rv1 = r1["stages"].get("remediation_validation")
        rv2 = r2["stages"].get("remediation_validation")
        assert rv1 == rv2


@pytest.mark.unit
class TestMLIsolation:
    """Verifica que módulos ML pesados não são importados durante testes."""

    def test_no_ml_imports_on_collection(self):
        """torch, faiss e sentence_transformers não estão em sys.modules."""
        ml_modules = ["torch", "sentence_transformers", "faiss"]
        loaded = [m for m in ml_modules if m in sys.modules]
        assert loaded == [], (
            f"Módulos ML importados indevidamente: {loaded}. "
            f"Testes devem rodar sem carregar dependências pesadas de ML."
        )
