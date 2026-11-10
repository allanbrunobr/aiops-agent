"""
Fixtures compartilhadas para o test suite do AIOps Agent.

Fornece dados simulados com seed-42 e helpers para rodar o pipeline
em modo --no-llm --no-rag sem efeitos colaterais de ML.
"""
from __future__ import annotations

# Deve estar no conftest top-level para pytest 9.x (não-top-level não é suportado)
pytest_plugins = ("anyio",)

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


# Todo teste precisa de um nível: é ele que decide em qual job do CI roda.
# Sem marcador, o teste só rodava no job rust-build, fora dos jobs rápidos (T-a).
TIER_MARKERS = ("unit", "integration", "snapshot", "rust")


def untiered_items(items) -> list[str]:
    """nodeids dos itens sem nenhum marcador de nível."""
    return [
        item.nodeid for item in items
        if not any(item.get_closest_marker(m) for m in TIER_MARKERS)
    ]


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config, items):
    """Portões de coleta: sem ML importado e todo teste com marcador de nível.

    tryfirst: roda antes da desseleção por -m, então vê a suíte inteira.
    """
    for mod in _ML_MODULES:
        if mod in sys.modules:
            raise ImportError(
                f"Módulo ML '{mod}' foi importado durante coleta de testes! "
                f"Isso viola AC #4. Use imports lazy ou modo --no-rag."
            )
    missing = untiered_items(items)
    if missing:
        listed = "\n  ".join(missing[:20])
        more = f"\n  ... e mais {len(missing) - 20}" if len(missing) > 20 else ""
        raise pytest.UsageError(
            f"{len(missing)} teste(s) sem marcador de nível {TIER_MARKERS}. "
            f"Sem ele o teste fica fora dos jobs rápidos do CI:\n  {listed}{more}"
        )


_REPO_MEMORY_DB = "data/incident_memory.db"
_MEMORY_DB_MODULES = ("rag.memory", "rag.indexer", "rag.retriever_history")


@pytest.fixture(autouse=True)
def _isolate_incident_memory_db(tmp_path, monkeypatch):
    """Nenhum teste grava na memória institucional do repositório.

    Desde que save_incident voltou a funcionar (F07), harnesses que rodam
    main() gravavam incidentes falsos em data/incident_memory.db — o arquivo
    que a CLI real lê como precedentes e chunks [HISTORICAL]. Os construtores
    têm o caminho como default de argumento, então o default é trocado por um
    arquivo em tmp_path, por teste.
    """
    import importlib

    target = str(tmp_path / "incident_memory.db")
    for mod_name in _MEMORY_DB_MODULES:
        mod = importlib.import_module(mod_name)
        for obj in vars(mod).values():
            if not (isinstance(obj, type) and obj.__module__ == mod_name):
                continue
            init = vars(obj).get("__init__")
            if init is None:
                continue
            if init.__defaults__ and _REPO_MEMORY_DB in init.__defaults__:
                monkeypatch.setattr(init, "__defaults__", tuple(
                    target if d == _REPO_MEMORY_DB else d for d in init.__defaults__))
            if init.__kwdefaults__ and _REPO_MEMORY_DB in init.__kwdefaults__.values():
                monkeypatch.setattr(init, "__kwdefaults__", {
                    k: (target if v == _REPO_MEMORY_DB else v)
                    for k, v in init.__kwdefaults__.items()})
    yield


@pytest.fixture
def isolated_cli(monkeypatch, tmp_path):
    """Roda main()/main_v1() sem efeitos externos nem vazamento de ambiente.

    Os entrypoints carregam `.env.local`/`.env` da raiz do repositório
    (config.env.load_local_env) e montam um SlackNotifier real quando há
    SLACK_BOT_TOKEN. Num checkout com `.env.local` de verdade, um teste que
    chama main() postaria no canal ou usaria a chave real. Aqui:
      - load_local_env aponta para um diretório vazio (nenhum arquivo real é lido);
      - o notifier é desligado (setup_slack_notifier → None);
      - variáveis de credencial já presentes são removidas;
      - os.environ inteiro é restaurado ao fim do teste.
    Não muda o diretório atual: a config é lida de config/defaults.yaml
    relativo ao cwd.
    """
    import os
    from unittest.mock import patch

    with patch.dict(os.environ, clear=False):
        for key in [k for k in os.environ
                    if k.startswith(("SLACK_", "ANTHROPIC_", "JIRA_", "DATABRICKS_"))]:
            del os.environ[key]
        monkeypatch.setattr("app.slack_setup.setup_slack_notifier",
                            lambda *args, **kwargs: None)
        monkeypatch.setattr("config.env.REPO_ROOT", tmp_path / "no-local-env")
        yield


@pytest.fixture(autouse=True)
def _isolate_external_side_effects(isolated_cli):
    """`isolated_cli` em TODO teste, não só nos que lembram de pedir.

    A revisão pré-PR achou testes de main() (test_api_key_safety.py) sem a
    fixture, que podiam postar no Slack real num checkout com `.env.local`.
    Depender de cada teste pedir é regra de obediência; isto é o portão.
    """
    yield


_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def _repo_output_files() -> set[str]:
    out = _REPO_ROOT / "output"
    return {str(p) for p in out.rglob("*") if p.is_file()} if out.is_dir() else set()


@pytest.fixture(autouse=True)
def _forbid_writes_to_repo_output():
    """Nenhum teste grava em output/ do repositório (relatório §2.4).

    Postmortems de teste iam para output/ do checkout. Teste que precisa
    gravar passa output_dir=tmp_path.
    """
    before = _repo_output_files()
    yield
    created = sorted(_repo_output_files() - before)
    if created:
        pytest.fail(
            "Teste gravou em output/ do repositório (use tmp_path): " + ", ".join(created[:5]),
            pytrace=False,
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
                    _label = ("Incidente resolvido" if tnr_check.result.value == "success"
                              else "Mitigação proposta, aguardando aprovação")
                    _rec("VERIFYING", "RESOLVED", f"{_label}: {tnr_check.message}")
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


# ---------------------------------------------------------------------------
# Fixtures para testes unitários de _tool_* do DiagnosisAgent (Story 2.2)
# ---------------------------------------------------------------------------

@pytest.fixture
def payload_correlate_signals():
    """Payload típico para _tool_correlate_signals."""
    return {"service": "payment-svc", "time_window_minutes": 5}


@pytest.fixture
def payload_reconstruct_timeline():
    """Payload típico para _tool_reconstruct_timeline."""
    return {"services": ["payment-svc"]}


@pytest.fixture
def payload_classify_fault_type():
    """Payload típico para _tool_classify_fault_type."""
    return {"service": "payment-svc"}


@pytest.fixture
def mock_aggregated_report_empty():
    """AggregatedReport mock com listas vazias para testes de tool methods."""
    from unittest.mock import MagicMock
    report = MagicMock()
    report.log_templates = []
    report.metric_anomalies = []
    report.slow_traces = []
    report.affected_services = set()
    return report


@pytest.fixture
def mock_aggregated_report_with_data():
    """AggregatedReport mock com dados mínimos para testes de tool methods."""
    from datetime import datetime
    from unittest.mock import MagicMock
    from pipeline.log_parser import LogTemplate
    from pipeline.metric_filter import MetricAnomaly
    from pipeline.trace_filter import SlowCallPath

    now = datetime(2024, 1, 1, 10, 5, 0)

    log_tpl = LogTemplate(
        template="Connection pool exhausted — max_connections limit reached",
        count=42,
        level="ERROR",
        services={"payment-svc"},
        first_seen=now,
        last_seen=now,
    )

    metric_anomaly = MetricAnomaly(
        service="payment-svc",
        metric_name="connection_pool",
        anomaly_type="spike",
        timestamp=now,
        value=100.0,
        mean=20.0,
        std=5.0,
        deviation_sigma=16.0,
        description="Connection pool spike",
    )

    slow_trace = SlowCallPath(
        trace_id="trace-001",
        path=["gateway", "payment-svc", "postgres"],
        operations=["GET /pay", "SELECT"],
        total_duration_ms=5000.0,
        bottleneck_service="payment-svc",
        bottleneck_duration_ms=4800.0,
        normal_duration_ms=200.0,
        timestamp=now,
        attributes={"pool": "exhausted"},
    )

    report = MagicMock()
    report.log_templates = [log_tpl]
    report.metric_anomalies = [metric_anomaly]
    report.slow_traces = [slow_trace]
    report.affected_services = {"payment-svc"}
    return report


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
