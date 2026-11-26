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
from contextlib import redirect_stderr, redirect_stdout

import pytest

import config.env as _config_env

# Nenhum teste lê o .env.local/.env do checkout — nem na coleta. Módulos que
# carregam o env no import (interfaces/demo/server.py) são importados no nível
# de módulo por arquivos de teste, antes de qualquer fixture rodar. A raiz
# aponta para um caminho que não existe; o fixture isolated_cli repete isso
# por teste (tmp_path).
_config_env.REPO_ROOT = _config_env.REPO_ROOT / "tests" / "_sem_env_local_"

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
TIER_MARKERS = ("unit", "integration", "snapshot", "rust", "e2e")


def untiered_items(items) -> list[str]:
    """nodeids dos itens sem nenhum marcador de nível."""
    return [
        item.nodeid
        for item in items
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
                monkeypatch.setattr(
                    init,
                    "__defaults__",
                    tuple(
                        target if d == _REPO_MEMORY_DB else d for d in init.__defaults__
                    ),
                )
            if init.__kwdefaults__ and _REPO_MEMORY_DB in init.__kwdefaults__.values():
                monkeypatch.setattr(
                    init,
                    "__kwdefaults__",
                    {
                        k: (target if v == _REPO_MEMORY_DB else v)
                        for k, v in init.__kwdefaults__.items()
                    },
                )
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
        for key in [
            k
            for k in os.environ
            if k.startswith(("SLACK_", "ANTHROPIC_", "JIRA_", "DATABRICKS_"))
        ]:
            del os.environ[key]
        monkeypatch.setattr(
            "app.slack_setup.setup_slack_notifier", lambda *args, **kwargs: None
        )
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


@pytest.fixture(autouse=True)
def _restore_logging_config():
    """Desfaz `configure_logging()` ao fim de cada teste.

    main(), main_v1() e os servidores chamam configure_logging(), que troca os
    handlers do root logger e a configuração global do structlog. Sem isto, o
    primeiro teste que chama main() muda o log de todos os seguintes.
    """
    import logging

    import structlog

    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    was_configured = structlog.is_configured()
    config = structlog.get_config()
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    if was_configured:
        structlog.configure(**config)
    else:
        structlog.reset_defaults()


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
            "Teste gravou em output/ do repositório (use tmp_path): "
            + ", ".join(created[:5]),
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
    """Executa o pipeline V1 (Blueprint Engine) em --no-llm --no-rag e retorna result dict.

    Helper reutilizado por pipeline_result e memory_leak_pipeline_result.
    Dirige o mesmo engine/pipeline do ``run_v1`` (app.builders) sem CLI parsing
    nem rich output, e converte o estado final no dict ``result`` via
    ``state_to_result`` (blueprint.adapters.state_adapter).

    Args:
        incident: SimulatedIncident com dados do cenário.
        scenario_name: Nome do cenário para incluir no result dict.
    """
    import argparse

    from adapters.credentials import EnvCredentialProvider
    from adapters.persistence.in_memory_store import InMemoryStore
    from app.builders import build_engine, build_pipeline
    from blueprint.adapters.state_adapter import state_to_result
    from blueprint.engine import PipelineAborted
    from config.loader import SafetyConfig, load_config
    from interfaces.cli.v1_runner import _state_after_abort, create_incident_state

    args = argparse.Namespace(no_llm=True, no_rag=True, compact=False, scenario=None)
    incident_data = {
        "logs": incident.logs,
        "metrics": incident.metrics,
        "traces": incident.traces,
    }

    # Suprime output do rich.Console durante execução do pipeline.
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        # espelha o PreExecutionValidator(max_blast_radius=3) do conftest legado
        config = load_config(None).model_copy(
            update={"safety": SafetyConfig(blast_radius_limit=3)}
        )
        engine = build_engine(config, InMemoryStore(), EnvCredentialProvider())
        pipeline = build_pipeline(config)
        state = create_incident_state(args, incident_data, incident=incident)
        try:
            state = engine.run(pipeline, state)
        except PipelineAborted as exc:
            aborted = _state_after_abort(exc, config)
            if aborted is None:
                raise
            state = aborted
        result = state_to_result(state)

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
    from data.registry import get_scenario
    from data.sample_incident import create_incident  # noqa: F401 — Registra todos os cenários.

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
    from data.registry import get_scenario
    from data.sample_incident import create_incident  # noqa: F401 — Registra todos os cenários.

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
    from pipeline.aggregator import Aggregator
    from pipeline.telemetry_sanitizer import TelemetrySanitizer

    incident = simulated_incident

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        sanitizer = TelemetrySanitizer(strict_mode=False)
        s_logs, s_metrics, s_traces, _ = sanitizer.sanitize_all(
            incident.logs,
            incident.metrics,
            incident.traces,
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
    from data.registry import get_scenario
    from data.sample_incident import create_incident  # noqa: F401

    scenario_name = request.param
    incident = get_scenario(scenario_name)(seed=42)
    result = _run_pipeline(incident, scenario_name=scenario_name)
    return {
        "scenario_name": scenario_name,
        "result": result,
        "incident": incident,
    }
