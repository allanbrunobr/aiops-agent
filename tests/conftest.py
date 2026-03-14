"""
Fixtures compartilhadas para o test suite do AIOps Agent.

Fornece dados simulados com seed-42 e helpers para rodar o pipeline
em modo --no-llm --no-rag sem efeitos colaterais de ML.
"""
from __future__ import annotations

import io
import random
import sys
from contextlib import redirect_stdout, redirect_stderr

import pytest

# Módulos ML que NUNCA devem ser importados durante coleta de testes
_ML_MODULES = ("torch", "sentence_transformers", "faiss")


def pytest_addoption(parser):
    """Registra opções customizadas para snapshot tests."""
    parser.addoption(
        "--snapshot-update",
        action="store_true",
        default=False,
        help="Regenera todos os arquivos de snapshot (sobrescreve existentes).",
    )


def pytest_collection_modifyitems(config, items):
    """Verifica que módulos ML pesados não foram importados durante coleta."""
    for mod in _ML_MODULES:
        if mod in sys.modules:
            raise ImportError(
                f"Módulo ML '{mod}' foi importado durante coleta de testes! "
                f"Isso viola AC #4. Use imports lazy ou modo --no-rag."
            )


@pytest.fixture(scope="class")
def simulated_incident():
    """Gera incidente simulado com seed-42 para output determinístico.

    Scope 'class' para compartilhar o mesmo incidente entre testes da classe.
    Seguro porque testes de smoke são read-only sobre o resultado.
    """
    random.seed(42)
    from data.sample_incident import create_incident
    return create_incident()


@pytest.fixture
def no_llm_args():
    """Retorna argparse.Namespace simulando --no-llm --no-rag.

    Nota: Preparado para stories futuras (1.2+). Não consumido em Story 1.1.
    """
    import argparse
    return argparse.Namespace(no_llm=True, no_rag=True, compact=False, scenario=None)


def _run_pipeline(incident, scenario_name: str | None = None):
    """Executa pipeline completo em modo --no-llm --no-rag e retorna result dict.

    Helper reutilizado por pipeline_result e memory_leak_pipeline_result.
    Espelha a sequência de main.main() sem CLI parsing nem rich output.

    Args:
        incident: SimulatedIncident com dados do cenário.
        scenario_name: Nome do cenário para incluir no result dict.
    """
    from pipeline.telemetry_sanitizer import TelemetrySanitizer
    from pipeline.aggregator import Aggregator
    from pipeline.context_compressor import ContextCompressor
    from agents.detector import DetectionAgent
    from agents.diagnoser import DiagnosisAgent
    from agents.mitigator import MitigationAgent
    from safety.tnr import TNRSafetyGuard
    from safety.pre_validator import PreExecutionValidator
    from safety.version_validator import VersionValidator

    # Suprime output do rich.Console durante execução do pipeline.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        # 1b. Sanitização (modifica incident in-place)
        sanitizer = TelemetrySanitizer(strict_mode=False)
        s_logs, s_metrics, s_traces, _san_report = sanitizer.sanitize_all(
            incident.logs, incident.metrics, incident.traces,
        )
        incident.logs = s_logs
        incident.metrics = s_metrics
        incident.traces = s_traces

        # 2. Pré-processamento
        aggregator = Aggregator()
        report = aggregator.process(incident.logs, incident.metrics, incident.traces)

        # 2b. Compressão de contexto
        compressor = ContextCompressor(compression_level="balanced")
        _compressed_text, _stats = compressor.compress(report)

        # 4. Pipeline de agentes (sem LLM)
        detector = DetectionAgent()
        diagnoser = DiagnosisAgent(max_iterations=1)
        mitigator = MitigationAgent()
        tnr_guard = TNRSafetyGuard()

        from datetime import datetime as _dt
        timeline: list[dict] = []

        def _rec(frm: str, to: str, reason: str) -> None:
            timeline.append({"time": _dt.now().strftime("%H:%M:%S"),
                             "from": frm, "to": to, "reason": reason})

        result: dict = {"success": False, "stages": {}}
        _rec("IDLE", "DETECTING", "Analisando dados de observabilidade...")
        detection = detector.detect(report)
        result["stages"]["detection"] = detection
        if detection.get("is_incident"):
            affected = ", ".join(detection.get("affected_services", []))
            _rec("DETECTING", "DIAGNOSING", f"Investigando incidente em {affected}")
            diagnosis = diagnoser.diagnose(report, None)
            result["stages"]["diagnosis"] = diagnosis
            if diagnosis.get("root_cause"):
                _rec("DIAGNOSING", "MITIGATING", "Propondo remediação...")
                mitigation = mitigator.propose(diagnosis)
                result["stages"]["mitigation"] = mitigation
                _rec("MITIGATING", "VERIFYING", "Verificando impacto (TNR)...")
                tnr_check = mitigator.verify_with_tnr(mitigation, tnr_guard)
                result["stages"]["verification"] = {
                    "result": tnr_check.result.value,
                    "health_delta": tnr_check.health_delta,
                    "message": tnr_check.message,
                }
                if tnr_check.result.value != "regression":
                    _rec("VERIFYING", "RESOLVED", f"Incidente resolvido: {tnr_check.message}")
                    result["success"] = True
                else:
                    _rec("VERIFYING", "ROLLBACK", f"Regressão detectada: {tnr_check.message}")
            else:
                _rec("DIAGNOSING", "FAILED", "Não foi possível identificar root cause")
        else:
            _rec("DETECTING", "IDLE", "Nenhum incidente detectado")
        result["timeline"] = timeline

        # 4b. Validação de segurança pré-execução
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

        # 4c. Validação de versão/CVE na remediação
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

    if scenario_name:
        result["scenario_name"] = scenario_name

    return result


@pytest.fixture(scope="class")
def pipeline_result(simulated_incident):
    """Executa pipeline completo em modo --no-llm --no-rag e retorna result dict.

    Scope 'class' para evitar reexecução redundante — testes são read-only.
    """
    return _run_pipeline(simulated_incident, scenario_name="payment-pool-exhaustion")


@pytest.fixture(scope="class")
def memory_leak_incident():
    """Gera incidente memory-leak com seed=42 para output determinístico.

    Scope 'class' para compartilhar o mesmo incidente entre testes da classe.
    Importa sample_incident para garantir que todos os cenários estão registrados.
    """
    from data.sample_incident import create_incident  # noqa: F401 — Registra todos os cenários.
    from data.registry import get_scenario
    return get_scenario("memory-leak")(seed=42)


@pytest.fixture(scope="class")
def memory_leak_pipeline_result(memory_leak_incident):
    """Executa pipeline completo com dados de memory-leak em modo --no-llm --no-rag.

    Scope 'class' para evitar reexecução redundante.
    """
    return _run_pipeline(memory_leak_incident, scenario_name="memory-leak")


@pytest.fixture(scope="class")
def unknown_fault_incident():
    """Gera incidente unknown-fault com seed=42 para output determinístico.

    Scope 'class' para compartilhar o mesmo incidente entre testes da classe.
    Importa sample_incident para garantir que todos os cenários estão registrados.
    """
    from data.sample_incident import create_incident  # noqa: F401 — Registra todos os cenários.
    from data.registry import get_scenario
    return get_scenario("unknown-fault")(seed=42)


@pytest.fixture(scope="class")
def unknown_fault_pipeline_result(unknown_fault_incident):
    """Executa pipeline completo com dados de unknown-fault em modo --no-llm --no-rag.

    Scope 'class' para evitar reexecução redundante.
    """
    return _run_pipeline(unknown_fault_incident, scenario_name="unknown-fault")


@pytest.fixture(scope="session")
def snapshot_manager(request):
    """Gerencia snapshots JSON com suporte a --snapshot-update.

    Scope 'session' — compartilhado entre todos os testes da sessão.
    Controla se snapshots são regenerados via flag --snapshot-update.
    """
    update = request.config.getoption("--snapshot-update", default=False)
    from tests.snapshot_utils import SnapshotManager
    return SnapshotManager(update=update)


@pytest.fixture(scope="class")
def aggregated_report(simulated_incident):
    """Executa sanitização + agregação e retorna AggregatedReport.

    Scope 'class' para compartilhar entre testes de pré-processamento.
    IMPORTANTE: Não usar na mesma classe que pipeline_result — ambos
    sanitizam simulated_incident in-place, causando double-sanitization.
    """
    from pipeline.telemetry_sanitizer import TelemetrySanitizer
    from pipeline.aggregator import Aggregator

    incident = simulated_incident

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        sanitizer = TelemetrySanitizer(strict_mode=False)
        s_logs, s_metrics, s_traces, _ = sanitizer.sanitize_all(
            incident.logs, incident.metrics, incident.traces,
        )
        incident.logs = s_logs
        incident.metrics = s_metrics
        incident.traces = s_traces

        aggregator = Aggregator()
        return aggregator.process(incident.logs, incident.metrics, incident.traces)


@pytest.fixture(scope="class")
def compression_stats(aggregated_report):
    """Executa compressão de contexto e retorna CompressionStats.

    Scope 'class' para compartilhar entre testes de snapshot.
    """
    from pipeline.context_compressor import ContextCompressor

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        compressor = ContextCompressor(compression_level="balanced")
        _compressed_text, stats = compressor.compress(aggregated_report)
        return stats


@pytest.fixture(
    params=["payment-pool-exhaustion", "memory-leak", "unknown-fault"],
    scope="class",
)
def scenario_pipeline_bundle(request):
    """Executa pipeline completo para cada cenário registrado.

    Parametrizado com scope='class' — roda pipeline uma vez por cenário
    por classe de testes. Retorna dict com scenario_name, result e incident.
    """
    from data.sample_incident import create_incident  # noqa: F401
    from data.registry import get_scenario

    scenario_name = request.param
    incident = get_scenario(scenario_name)(seed=42)
    result = _run_pipeline(incident, scenario_name=scenario_name)
    return {
        "scenario_name": scenario_name,
        "result": result,
        "incident": incident,
    }
