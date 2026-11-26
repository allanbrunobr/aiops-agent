"""A4 — suíte de failure-injection (ROBUSTNESS_PLAN.md §3 A4).

Cada cenário injeta uma falha REAL (mock que levanta exceção, não MagicMock
passivo) no ``main()`` de ponta a ponta e afirma o estado seguro: exit code
fail-closed e nunca um falso sucesso. Sem torch real, sem rede, sem Databricks:
tudo roda no job integration. Postmortems vão para ``tmp_path``.

Cenário 4 (falha de load do modelo) asserta apenas o INVARIANTE "sem falso
sucesso", estável antes e depois de B1: hoje exit 1; após B1, exit ∈ {0,3} com
``rag_degraded`` registrado.
"""

from __future__ import annotations

import io
import json
import math
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from main import EXIT_APPROVAL_NEEDED, EXIT_ERROR, EXIT_SUCCESS, main

pytestmark = pytest.mark.integration

VALID_EXITS = {EXIT_SUCCESS, EXIT_APPROVAL_NEEDED}


def _run_main(argv: list[str]) -> int:
    """Executa ``main()`` com argv dado, silenciando stdout/stderr."""
    with (
        patch("sys.argv", ["main.py", *argv]),
        patch("main.load_local_env"),
        redirect_stdout(io.StringIO()),
        redirect_stderr(io.StringIO()),
    ):
        return main()


def _postmortems(out: Path) -> list[Path]:
    return sorted(out.glob("postmortem_*"))


def _postmortem_json(out: Path) -> dict:
    files = sorted(out.glob("postmortem_*.json"))
    assert files, f"nenhum postmortem JSON em {out}"
    return json.loads(files[-1].read_text())


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem chaves/endpoints reais herdados do ambiente do desenvolvedor."""
    for var in (
        "DATABRICKS_HOST",
        "DATABRICKS_TOKEN",
        "DATABRICKS_WAREHOUSE_ID",
        "SLACK_BOT_TOKEN",
        "SLACK_WEBHOOK_URL",
    ):
        monkeypatch.delenv(var, raising=False)


# ── 1. LLM timeout → fallback heurístico ─────────────────────────────────────


def _failing_anthropic(exc: Exception) -> MagicMock:
    """Classe ``Anthropic`` falsa cujo ``messages.create`` levanta ``exc``."""
    client = MagicMock()
    client.messages.create.side_effect = exc
    return MagicMock(return_value=client)


def test_llm_timeout_falls_back_to_heuristic_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    from anthropic import APITimeoutError

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    timeout = APITimeoutError(request=httpx.Request("POST", "https://x.invalid"))
    fake = _failing_anthropic(timeout)

    with patch("adapters.llm.anthropic_client.Anthropic", fake):
        code = _run_main(["--output-dir", str(tmp_path), "--output-format", "json"])

    assert fake.return_value.messages.create.called, (
        "LLM nunca foi chamado: sem injeção"
    )
    assert code in VALID_EXITS, f"timeout do LLM virou exit {code} (esperado fallback)"
    diagnosis = _postmortem_json(tmp_path)["diagnosis"]
    assert diagnosis.get("root_cause"), "fallback heurístico sem diagnóstico"


# ── 2. LLM budget → aborta com EXIT_ERROR, nunca sucesso ─────────────────────


def test_llm_budget_exceeded_aborts_with_exit_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.llm_json import LLMBudgetExceeded

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    fake = _failing_anthropic(LLMBudgetExceeded("LLM call limit exceeded: 16 > 15"))

    with patch("adapters.llm.anthropic_client.Anthropic", fake):
        code = _run_main(["--output-dir", str(tmp_path), "--output-format", "json"])

    assert fake.return_value.messages.create.called
    assert code == EXIT_ERROR, f"estouro de orçamento deu exit {code}, não EXIT_ERROR"
    assert not _postmortems(tmp_path), (
        "run abortado não pode emitir postmortem de sucesso"
    )


# ── 3. RAG vazio → sem crash ─────────────────────────────────────────────────


def test_empty_rag_results_do_not_crash(tmp_path: Path) -> None:
    retriever = MagicMock()
    retriever.search.return_value = []

    with patch("interfaces.cli.v0_pipeline.step_rag", return_value=retriever):
        code = _run_main(
            ["--no-llm", "--output-dir", str(tmp_path), "--output-format", "json"]
        )

    assert code in VALID_EXITS, f"RAG vazio derrubou o pipeline (exit {code})"
    assert _postmortem_json(tmp_path)["diagnosis"].get("root_cause")


# ── 4. Falha de load do modelo → sem falso sucesso (invariante B1-estável) ───


def test_model_load_failure_never_reports_false_success(tmp_path: Path) -> None:
    from rag.indexer import ObservabilityIndexer

    boom = RuntimeError("model unavailable (offline)")
    with patch.object(ObservabilityIndexer, "_load_model", side_effect=boom) as load:
        code = _run_main(
            [
                "--no-llm",
                "--rebuild-rag",
                "--output-dir",
                str(tmp_path),
                "--output-format",
                "json",
            ]
        )

    assert load.called, "_load_model nunca foi chamado: sem injeção"
    # B1: degradação graciosa — sem RAG o agente segue, registrando a degradação.
    assert code in VALID_EXITS, f"exit inesperado {code}"
    pm = _postmortem_json(tmp_path)
    assert pm["meta"].get("rag_degraded") is True, (
        "falha do modelo engolida sem registrar degradação (falso sucesso)"
    )


# ── 5. Telemetria malformada → None ou score limitado, sem crash ─────────────


def _anomaly(value: float, i: int, **over) -> SimpleNamespace:
    base = dict(
        service="svc",
        metric_name="cpu",
        timestamp=datetime(2026, 1, 1) + timedelta(minutes=i),
        value=value,
        mean=50.0,
        std=5.0,
        deviation_sigma=6.0,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _assert_bounded(pred) -> None:
    if pred is None:
        return
    assert math.isfinite(pred.risk_score), f"risk_score não finito: {pred.risk_score}"
    assert 0.0 <= pred.risk_score <= 100.0, (
        f"risk_score fora de [0,100]: {pred.risk_score}"
    )


@pytest.mark.parametrize(
    "anomalies",
    [
        [_anomaly(float("nan"), i) for i in range(8)],
        [_anomaly(float("inf") if i == 4 else 60.0 + i, i) for i in range(8)],
        [_anomaly(60.0 + i, i, mean=float("nan"), std=float("nan")) for i in range(8)],
        [],
    ],
    ids=["all-nan", "one-inf", "nan-baseline", "empty"],
)
def test_malformed_telemetry_yields_none_or_bounded_score(anomalies: list) -> None:
    from pipeline.predictive_stage import run_predictive_stage

    report = SimpleNamespace(
        metric_anomalies=anomalies, log_templates=[], slow_traces=[]
    )
    _assert_bounded(run_predictive_stage(report))


def test_missing_fields_and_empty_lists_do_not_crash() -> None:
    from pipeline.predictive_stage import run_predictive_stage

    _assert_bounded(run_predictive_stage(SimpleNamespace()))
    _assert_bounded(
        run_predictive_stage(
            SimpleNamespace(
                metric_anomalies=[SimpleNamespace() for _ in range(6)],
                log_templates=[SimpleNamespace()],
                slow_traces=[SimpleNamespace()],
            )
        )
    )


def test_pipeline_survives_nan_metric_points(tmp_path: Path) -> None:
    """E2E: pontos NaN injetados na telemetria não crasham nem viram sucesso falso."""
    import data.sample_incident as sample

    incident = sample.create_incident()
    from data.models import MetricPoint

    t0 = incident.metrics[0].timestamp
    incident.metrics.extend(
        MetricPoint(
            timestamp=t0 + timedelta(seconds=i),
            service="payment-svc",
            metric_name="db_pool_active",
            value=float("nan"),
        )
        for i in range(10)
    )
    with patch(
        "interfaces.cli.v0_pipeline.step_generate_data",
        return_value=(incident, "nan-injected"),
    ):
        code = _run_main(
            [
                "--no-llm",
                "--no-rag",
                "--output-dir",
                str(tmp_path),
                "--output-format",
                "json",
            ]
        )

    assert code in VALID_EXITS | {EXIT_ERROR}
    if code in VALID_EXITS:
        pred = _postmortem_json(tmp_path).get("prediction") or {}
        score = pred.get("risk_score")
        assert score is None or (math.isfinite(score) and 0 <= score <= 100)


# ── 6. Conector fora → fallback por fonte / EXIT_ERROR ───────────────────────


def _dbx_error() -> Exception:
    from tools.databricks_connector import DatabricksConnectorError

    return DatabricksConnectorError(
        service="databricks",
        error_type="timeout",
        timestamp="2026-01-01T00:00:00Z",
        details="connector down",
    )


def test_each_connector_failure_falls_back_per_source() -> None:
    from interfaces.cli.data_sources import step_fetch_databricks_data

    logs, metrics, traces = MagicMock(), MagicMock(), MagicMock()
    logs.search.side_effect = _dbx_error()
    metrics.query.return_value = [
        {
            "timestamp": "2026-01-01T00:00:00",
            "service": "s",
            "metric": "m",
            "value": 1.0,
        }
    ]
    traces.find_traces.side_effect = _dbx_error()

    incident, name = step_fetch_databricks_data(logs, metrics, traces)

    assert name == "databricks-live"
    assert incident.logs == [] and incident.traces == []
    assert len(incident.metrics) == 1, "falha de uma fonte derrubou as outras"


def test_missing_connector_credentials_exit_error(tmp_path: Path) -> None:
    code = _run_main(
        [
            "--no-llm",
            "--no-rag",
            "--data-source",
            "databricks",
            "--output-dir",
            str(tmp_path),
        ]
    )
    assert code == EXIT_ERROR
    assert not _postmortems(tmp_path)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG CONHECIDO (achado em A4): com todas as fontes Databricks fora o "
        "pipeline roda sobre telemetria vazia, não detecta incidente e sai com "
        "exit 0 ('resolved') — falso sucesso. Fora do write-set de A4; corrigir "
        "(ex.: B8/fail-closed em step_fetch_databricks_data) e remover este xfail."
    ),
)
def test_all_connectors_down_never_reports_success(tmp_path: Path) -> None:
    """Todas as fontes levantam DatabricksConnectorError: sem dados ≠ sucesso."""
    down = MagicMock()
    down.search.side_effect = _dbx_error()
    down.query.side_effect = _dbx_error()
    down.find_traces.side_effect = _dbx_error()

    with patch(
        "interfaces.cli.v0_pipeline._create_databricks_sources",
        return_value=(down, down, down),
    ):
        code = _run_main(
            [
                "--no-llm",
                "--no-rag",
                "--data-source",
                "databricks",
                "--output-dir",
                str(tmp_path),
                "--output-format",
                "json",
            ]
        )

    assert down.search.called and down.query.called and down.find_traces.called
    assert code != EXIT_SUCCESS, "todas as fontes fora e o run reportou sucesso"
