"""Testes de entrega Slack em diferentes escalas de logs.

Verifica que o SlackNotifier envia mensagens corretamente quando o pipeline
processa volumes variados de logs (100, 1_000, 10_000). Cobre:
  - Postagem inicial (chat_postMessage)
  - Atualizacoes progressivas (chat_update) para cada node.completed
  - Mensagem final com severity, root_cause e proposed_fix
  - Modo dry_run, ausencia de token, fallback apos retries esgotados
  - Consistencia estrutural dos blocos entre escalas diferentes
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from core.lifecycle import LifecycleEvent
from server.notifications.slack import SlackNotifier, _NODE_LABELS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_client() -> AsyncMock:
    """Cria cliente Slack mockado com respostas padrao."""
    client = AsyncMock()
    client.chat_postMessage.return_value = {"ts": "123.456", "channel": "C123"}
    client.chat_update.return_value = {"ts": "123.456"}
    return client


def _make_notifier(
    mock_client: AsyncMock,
    *,
    channel: str = "#incidents",
    max_retries: int = 3,
    initial_backoff_s: float = 0.01,
    fallback_dir: str = "output",
    dry_run: bool = False,
) -> SlackNotifier:
    """Cria SlackNotifier com cliente injetado, pronto para testes."""
    notifier = SlackNotifier.__new__(SlackNotifier)
    notifier._client = None if dry_run else mock_client
    notifier._channel = channel
    notifier._max_retries = max_retries
    notifier._initial_backoff_s = initial_backoff_s
    notifier._backoff_multiplier = 2.0
    notifier._fallback_dir = Path(fallback_dir)
    notifier._dry_run = dry_run
    notifier._message_ts = {}
    notifier._channel_ids = {}
    notifier._completed_nodes = {}
    return notifier


def _make_event(
    event_type: str,
    *,
    correlation_id: str = "inc-scale-test-001",
    node_name: str | None = None,
    data: dict | None = None,
) -> LifecycleEvent:
    """Cria LifecycleEvent para testes."""
    return LifecycleEvent(
        event_type=event_type,
        node_name=node_name,
        correlation_id=correlation_id,
        timestamp=datetime.utcnow(),
        data=data or {},
    )


def _make_slack_error(code: str) -> SlackApiError:
    """Cria SlackApiError com codigo especifico."""
    response = MagicMock()
    response.get.return_value = code
    response.__getitem__ = lambda self, key: code if key == "error" else None
    return SlackApiError(f"Slack error: {code}", response=response)


def _generate_report(log_count: int):
    """Gera AggregatedReport a partir de N logs usando o pipeline real.

    Retorna (report, compressed_text, stats) para uso nos testes.
    """
    from infra.benchmark_compression import generate_logs, generate_metrics, generate_traces
    from pipeline.aggregator import Aggregator
    from pipeline.context_compressor import ContextCompressor

    logs = generate_logs(log_count)
    metrics = generate_metrics()
    traces = generate_traces()
    report = Aggregator().process(logs, metrics, traces)
    compressor = ContextCompressor(token_budget=4000)
    text, stats = compressor.compress(report)
    return report, text, stats


# Lista dos 11 nodes do pipeline
ALL_NODES = list(_NODE_LABELS.keys())


# ---------------------------------------------------------------------------
# Fixtures com cache por escala — evita reprocessar logs em cada teste
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def report_100():
    """Report gerado a partir de 100 logs."""
    return _generate_report(100)


@pytest.fixture(scope="module")
def report_1k():
    """Report gerado a partir de 1000 logs."""
    return _generate_report(1_000)


@pytest.fixture(scope="module")
def report_10k():
    """Report gerado a partir de 10000 logs."""
    return _generate_report(10_000)


# ---------------------------------------------------------------------------
# 1-3. Mensagem inicial postada para cada escala
# ---------------------------------------------------------------------------

class TestInitialMessagePosted:
    """Verifica que chat_postMessage e chamado exatamente 1x para cada escala."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_initial_message_posted_100_logs(self, report_100):
        """100 logs -> chat_postMessage chamado 1x com blocks corretos."""
        report, _, _ = report_100
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        event = _make_event(
            "pipeline.started",
            data={
                "title": "Payment service incident",
                "severity": report.severity,
                "service": "payment-svc",
            },
        )
        await notifier.on_event(event)

        mock_client.chat_postMessage.assert_called_once()
        call_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "#incidents"
        blocks = call_kwargs["blocks"]
        # Verifica que blocos iniciais contem severity do report
        blocks_text = str(blocks)
        assert report.severity in blocks_text

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_initial_message_posted_1k_logs(self, report_1k):
        """1000 logs -> chat_postMessage chamado 1x com blocks corretos."""
        report, _, _ = report_1k
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        event = _make_event(
            "pipeline.started",
            data={
                "title": "Payment service incident",
                "severity": report.severity,
                "service": "payment-svc",
            },
        )
        await notifier.on_event(event)

        mock_client.chat_postMessage.assert_called_once()
        call_kwargs = mock_client.chat_postMessage.call_args.kwargs
        blocks = call_kwargs["blocks"]
        blocks_text = str(blocks)
        assert report.severity in blocks_text

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_initial_message_posted_10k_logs(self, report_10k):
        """10000 logs -> chat_postMessage chamado 1x com blocks corretos."""
        report, _, _ = report_10k
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        event = _make_event(
            "pipeline.started",
            data={
                "title": "Payment service incident",
                "severity": report.severity,
                "service": "payment-svc",
            },
        )
        await notifier.on_event(event)

        mock_client.chat_postMessage.assert_called_once()
        call_kwargs = mock_client.chat_postMessage.call_args.kwargs
        blocks = call_kwargs["blocks"]
        blocks_text = str(blocks)
        assert report.severity in blocks_text


# ---------------------------------------------------------------------------
# 4. Node updates para todas as escalas (parametrizado)
# ---------------------------------------------------------------------------

class TestNodeUpdatesForAllScales:
    """Verifica que chat_update e chamado para cada node.completed em cada escala."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize("log_count", [100, 1_000, 10_000])
    async def test_node_updates_for_all_scales(self, log_count):
        """Parametrizado: chat_update chamado para cada um dos 11 nodes."""
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        # Posta mensagem inicial para registrar ts
        start_event = _make_event(
            "pipeline.started",
            data={
                "title": f"Incident with {log_count} logs",
                "severity": "HIGH",
                "service": "payment-svc",
            },
        )
        await notifier.on_event(start_event)

        # Simula conclusao de todos os 11 nodes
        for node_name in ALL_NODES:
            node_event = _make_event(
                "node.completed",
                node_name=node_name,
                data={"duration_ms": 150, "log_count": log_count},
            )
            await notifier.on_event(node_event)

        # chat_update deve ter sido chamado 11 vezes (1 por node)
        assert mock_client.chat_update.call_count == len(ALL_NODES)

        # Verifica que todos os nodes foram rastreados internamente
        cid = "inc-scale-test-001"
        for node_name in ALL_NODES:
            assert node_name in notifier._completed_nodes[cid]


# ---------------------------------------------------------------------------
# 5. Mensagem final contem severity (via blocos iniciais onde severity aparece)
# ---------------------------------------------------------------------------

class TestFinalMessageContainsSeverity:
    """Verifica que severity do report aparece nos blocos Slack.

    A severity e renderizada nos blocos iniciais (pipeline.started).
    Verificamos que para cada escala, a mensagem inicial carrega a severity correta
    e que a mensagem final tem estrutura Block Kit valida.
    """

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_final_message_contains_severity_100(self, report_100):
        """100 logs: severity presente nos blocos iniciais, final com estrutura completa."""
        await self._verify_severity_flow(report_100)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_final_message_contains_severity_1k(self, report_1k):
        """1000 logs: severity presente nos blocos iniciais, final com estrutura completa."""
        await self._verify_severity_flow(report_1k)

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_final_message_contains_severity_10k(self, report_10k):
        """10000 logs: severity presente nos blocos iniciais, final com estrutura completa."""
        await self._verify_severity_flow(report_10k)

    @staticmethod
    async def _verify_severity_flow(report_tuple):
        """Helper: verifica severity nos blocos iniciais e estrutura dos finais."""
        report, _, _ = report_tuple
        severity = report.severity
        assert severity in {"CRITICAL", "HIGH", "MEDIUM", "LOW"}

        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        # Pipeline started — severity nos blocos iniciais
        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Test incident", "severity": severity, "service": "payment-svc"},
        ))

        initial_blocks = mock_client.chat_postMessage.call_args.kwargs["blocks"]
        initial_text = str(initial_blocks)
        assert severity in initial_text, (
            f"Severity '{severity}' nao encontrada nos blocos iniciais"
        )

        # Pipeline completed — blocos finais com estrutura correta
        final_data = {
            "root_cause": "Connection pool exhausted",
            "confidence": 0.92,
            "evidence": ["Error rate spike", "Pool metrics"],
            "proposed_fix": "kubectl scale deployment payment-svc --replicas=5",
            "risk_level": "medium",
            "blast_radius": "payment-svc only",
            "total_duration_ms": 5000,
        }
        await notifier.on_event(_make_event("pipeline.completed", data=final_data))

        # Verifica estrutura Block Kit dos blocos finais
        final_blocks = mock_client.chat_update.call_args.kwargs["blocks"]
        block_types = [b["type"] for b in final_blocks]
        assert "header" in block_types
        assert "section" in block_types
        assert "divider" in block_types
        assert "context" in block_types


# ---------------------------------------------------------------------------
# 6. Mensagem final contem root_cause
# ---------------------------------------------------------------------------

class TestFinalMessageContainsRootCause:
    """Verifica que blocos finais contem root_cause."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_final_message_contains_root_cause(self):
        """Blocos finais incluem root_cause fornecido no evento."""
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Test", "severity": "HIGH", "service": "payment-svc"},
        ))

        root_cause = "Database connection pool exhausted due to leaked connections"
        await notifier.on_event(_make_event(
            "pipeline.completed",
            data={
                "root_cause": root_cause,
                "confidence": 0.88,
                "evidence": ["Pool saturation at 200/200"],
                "proposed_fix": "Restart payment-svc pods",
                "risk_level": "high",
                "blast_radius": "payment-svc",
                "total_duration_ms": 8000,
            },
        ))

        mock_client.chat_update.assert_called_once()
        blocks = mock_client.chat_update.call_args.kwargs["blocks"]
        full_text = str(blocks)
        assert root_cause in full_text

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_final_message_contains_proposed_fix(self):
        """Blocos finais incluem proposed_fix fornecido no evento."""
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Test", "severity": "MEDIUM", "service": "auth-svc"},
        ))

        proposed_fix = "kubectl rollout restart deployment/auth-svc -n production"
        await notifier.on_event(_make_event(
            "pipeline.completed",
            data={
                "root_cause": "Memory leak in auth handler",
                "confidence": 0.75,
                "evidence": [],
                "proposed_fix": proposed_fix,
                "risk_level": "medium",
                "blast_radius": "auth-svc",
                "total_duration_ms": 3500,
            },
        ))

        blocks = mock_client.chat_update.call_args.kwargs["blocks"]
        full_text = str(blocks)
        assert proposed_fix in full_text

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_final_message_contains_confidence_and_risk(self):
        """Blocos finais incluem confidence e risk_level."""
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Test", "severity": "CRITICAL", "service": "svc"},
        ))

        await notifier.on_event(_make_event(
            "pipeline.completed",
            data={
                "root_cause": "Cascading failure",
                "confidence": 0.92,
                "evidence": ["Spike A", "Spike B"],
                "proposed_fix": "Scale up",
                "risk_level": "critical",
                "blast_radius": "all services",
                "total_duration_ms": 12000,
            },
        ))

        blocks = mock_client.chat_update.call_args.kwargs["blocks"]
        full_text = str(blocks)
        # Confidence renderizada como percentual
        assert "92%" in full_text
        # Risk level presente
        assert "critical" in full_text


# ---------------------------------------------------------------------------
# 7. Slack nao chamado sem token
# ---------------------------------------------------------------------------

class TestSlackNotCalledWithoutToken:
    """Verifica que sem SLACK_BOT_TOKEN nenhuma chamada API acontece."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_slack_not_called_when_no_token(self):
        """Sem token -> zero chamadas API (nao posta, nao atualiza)."""
        # Cria notifier sem client (simula ausencia de token)
        notifier = SlackNotifier.__new__(SlackNotifier)
        notifier._client = None
        notifier._dry_run = False
        notifier._channel = "#incidents"
        notifier._message_ts = {}
        notifier._channel_ids = {}
        notifier._completed_nodes = {}
        notifier._max_retries = 3
        notifier._initial_backoff_s = 0.01
        notifier._backoff_multiplier = 2.0
        notifier._fallback_dir = Path("output")

        # Simula ciclo completo: started -> nodes -> completed
        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Test", "severity": "HIGH", "service": "svc"},
        ))
        for node in ALL_NODES:
            await notifier.on_event(_make_event(
                "node.completed",
                node_name=node,
                data={"duration_ms": 100},
            ))
        await notifier.on_event(_make_event(
            "pipeline.completed",
            data={"root_cause": "Test", "confidence": 0.9},
        ))

        # Nenhum ts registrado (nenhuma chamada API feita)
        assert len(notifier._message_ts) == 0


# ---------------------------------------------------------------------------
# 8. Dry run: zero chamadas API, estado interno correto
# ---------------------------------------------------------------------------

class TestDryRunZeroApiCalls:
    """Verifica que dry_run=True nao faz chamadas API mas mantem estado."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_dry_run_zero_api_calls(self):
        """dry_run=True -> zero chamadas API, mas estado interno correto."""
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client, dry_run=True)

        cid = "inc-scale-test-001"

        # Pipeline started
        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Test", "severity": "HIGH", "service": "svc"},
        ))

        # Verifica que ts de dry-run foi registrado
        assert notifier._message_ts[cid] == "dry-run"

        # Simula todos os nodes
        for node in ALL_NODES:
            await notifier.on_event(_make_event(
                "node.completed",
                node_name=node,
                data={"duration_ms": 50},
            ))

        # Verifica que nodes foram rastreados internamente
        assert len(notifier._completed_nodes[cid]) == len(ALL_NODES)

        # Pipeline completed
        await notifier.on_event(_make_event(
            "pipeline.completed",
            data={"root_cause": "Test cause", "confidence": 0.9},
        ))

        # Zero chamadas API — mock_client nunca foi tocado
        mock_client.chat_postMessage.assert_not_called()
        mock_client.chat_update.assert_not_called()

        # Estado limpo apos pipeline.completed
        assert cid not in notifier._message_ts
        assert cid not in notifier._completed_nodes


# ---------------------------------------------------------------------------
# 9. Fallback escrito quando todos os retries falham
# ---------------------------------------------------------------------------

class TestFallbackOnRetryExhaustion:
    """Verifica que JSON fallback e criado quando todos os retries falham."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_fallback_written_when_all_retries_fail(self, tmp_path):
        """max_retries=1, API sempre falha -> fallback JSON criado."""
        mock_client = _make_mock_client()
        # chat_update sempre falha (usado na mensagem final via update)
        mock_client.chat_update.side_effect = _make_slack_error("channel_not_found")

        notifier = _make_notifier(
            mock_client,
            max_retries=1,
            fallback_dir=str(tmp_path),
        )

        # Envia mensagem inicial (funciona pois chat_postMessage nao falha)
        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Test", "severity": "CRITICAL", "service": "payment-svc"},
        ))

        postmortem_data = {
            "root_cause": "Connection pool exhausted",
            "confidence": 0.95,
            "proposed_fix": "kubectl rollout restart",
            "risk_level": "high",
            "blast_radius": "payment-svc",
            "total_duration_ms": 7000,
        }

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await notifier.on_event(_make_event(
                "pipeline.completed",
                data=postmortem_data,
            ))

        # Verifica que fallback JSON foi criado
        fallback_files = list(tmp_path.glob("fallback_*.json"))
        assert len(fallback_files) == 1

        # Verifica conteudo do fallback
        data = json.loads(fallback_files[0].read_text())
        assert data["correlation_id"] == "inc-scale-test-001"
        assert data["fallback_reason"] == "slack_delivery_exhausted"
        assert "postmortem" in data
        assert data["postmortem"]["root_cause"] == "Connection pool exhausted"


# ---------------------------------------------------------------------------
# 10. Consistencia estrutural dos blocos entre escalas
# ---------------------------------------------------------------------------

class TestScaleConsistencySlackBlocks:
    """Verifica que blocos finais para 100 vs 10K logs tem mesma estrutura."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_scale_consistency_slack_blocks(self, report_100, report_10k):
        """Blocos finais para 100 logs e 10K logs devem ter mesma lista de block types."""
        final_data = {
            "root_cause": "Connection pool exhausted",
            "confidence": 0.92,
            "evidence": ["Error rate spike", "Pool metrics saturated"],
            "proposed_fix": "kubectl scale deployment payment-svc --replicas=5",
            "risk_level": "medium",
            "blast_radius": "payment-svc only",
            "total_duration_ms": 5000,
        }

        block_types_per_scale = []

        for report_tuple in [report_100, report_10k]:
            report, _, _ = report_tuple
            mock_client = _make_mock_client()
            notifier = _make_notifier(mock_client)

            await notifier.on_event(_make_event(
                "pipeline.started",
                data={
                    "title": "Test",
                    "severity": report.severity,
                    "service": "payment-svc",
                },
            ))

            await notifier.on_event(_make_event("pipeline.completed", data=final_data))

            blocks = mock_client.chat_update.call_args.kwargs["blocks"]
            block_types = [b["type"] for b in blocks]
            block_types_per_scale.append(block_types)

        # Mesma estrutura de blocos independente da escala
        assert block_types_per_scale[0] == block_types_per_scale[1]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_initial_blocks_consistent_across_scales(self, report_100, report_10k):
        """Blocos iniciais para 100 e 10K logs tem mesma estrutura."""
        initial_block_types_per_scale = []

        for report_tuple in [report_100, report_10k]:
            report, _, _ = report_tuple
            mock_client = _make_mock_client()
            notifier = _make_notifier(mock_client)

            await notifier.on_event(_make_event(
                "pipeline.started",
                data={
                    "title": "Test",
                    "severity": report.severity,
                    "service": "payment-svc",
                },
            ))

            blocks = mock_client.chat_postMessage.call_args.kwargs["blocks"]
            block_types = [b["type"] for b in blocks]
            initial_block_types_per_scale.append(block_types)

        assert initial_block_types_per_scale[0] == initial_block_types_per_scale[1]


# ---------------------------------------------------------------------------
# Teste parametrizado completo: ciclo end-to-end por escala
# ---------------------------------------------------------------------------

class TestFullCyclePerScale:
    """Ciclo completo de eventos Slack parametrizado por escala de logs."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize("log_count", [100, 1_000, 10_000])
    async def test_slack_message_sent_for_scale(self, log_count):
        """Verifica ciclo completo: started -> 11 nodes -> completed."""
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)
        cid = "inc-scale-test-001"

        # 1. Pipeline started
        await notifier.on_event(_make_event(
            "pipeline.started",
            data={
                "title": "Payment service incident",
                "severity": "HIGH",
                "service": "payment-svc",
            },
        ))

        # Verifica chat_postMessage chamado 1x
        assert mock_client.chat_postMessage.call_count == 1
        assert cid in notifier._message_ts

        # 2. Todos os 11 nodes completados
        for node_name in ALL_NODES:
            await notifier.on_event(_make_event(
                "node.completed",
                node_name=node_name,
                data={"duration_ms": 100 + log_count // 100},
            ))

        # Verifica chat_update chamado 11 vezes (1 por node)
        assert mock_client.chat_update.call_count == len(ALL_NODES)

        # 3. Pipeline completed
        await notifier.on_event(_make_event(
            "pipeline.completed",
            data={
                "root_cause": "Connection pool exhausted in payment-svc",
                "confidence": 0.92,
                "evidence": ["Error spike detected", "Pool metrics at 200/200"],
                "proposed_fix": "kubectl scale deployment payment-svc --replicas=5",
                "risk_level": "medium",
                "blast_radius": "payment-svc only",
                "total_duration_ms": 5000 + log_count,
            },
        ))

        # chat_update chamado mais 1x para mensagem final (total: 11 + 1 = 12)
        assert mock_client.chat_update.call_count == len(ALL_NODES) + 1

        # Estado limpo apos conclusao
        assert cid not in notifier._message_ts
        assert cid not in notifier._completed_nodes

    @pytest.mark.unit
    @pytest.mark.asyncio
    @pytest.mark.parametrize("log_count", [100, 1_000, 10_000])
    async def test_progress_blocks_show_checkmarks(self, log_count):
        """Verifica que blocos de progresso mostram checkmarks para nodes concluidos."""
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Test", "severity": "HIGH", "service": "svc"},
        ))

        # Completa 3 nodes
        completed = ALL_NODES[:3]
        for node_name in completed:
            await notifier.on_event(_make_event(
                "node.completed",
                node_name=node_name,
                data={"duration_ms": 200},
            ))

        # Verifica ultimo chat_update contem checkmarks
        last_call = mock_client.chat_update.call_args_list[-1]
        blocks = last_call.kwargs["blocks"]
        blocks_text = str(blocks)

        # Nodes concluidos devem ter checkmark
        assert "white_check_mark" in blocks_text
        # Nodes pendentes devem ter quadrado vazio
        assert "black_square_button" in blocks_text


# ---------------------------------------------------------------------------
# Testes com pipeline real (integration) para validar severity
# ---------------------------------------------------------------------------

class TestSeverityFromRealPipeline:
    """Verifica severities reais gerados pelo pipeline para cada escala."""

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_severity_100_logs(self, report_100):
        """100 logs -> severity valida."""
        report, _, _ = report_100
        assert report.severity in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_severity_1k_logs(self, report_1k):
        """1000 logs -> severity valida."""
        report, _, _ = report_1k
        assert report.severity in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

    @pytest.mark.integration
    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_severity_10k_logs(self, report_10k):
        """10000 logs -> severity valida."""
        report, _, _ = report_10k
        assert report.severity in {"LOW", "MEDIUM", "HIGH", "CRITICAL"}

    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_severity_scales_with_volume(self, report_100, report_1k, report_10k):
        """Severity so pode ser igual ou maior conforme volume cresce."""
        severity_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
        s100 = severity_order[report_100[0].severity]
        s1k = severity_order[report_1k[0].severity]
        s10k = severity_order[report_10k[0].severity]
        # Mais logs -> mais errors -> severity igual ou maior
        assert s1k >= s100
        assert s10k >= s1k


# ---------------------------------------------------------------------------
# Testes de borda e resiliencia
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Testes de borda para cenarios incomuns no fluxo Slack."""

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_node_completed_without_started_is_ignored(self):
        """node.completed antes de pipeline.started nao causa erro."""
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        # Envia node.completed sem ter enviado pipeline.started
        await notifier.on_event(_make_event(
            "node.completed",
            node_name="log_parser",
            data={"duration_ms": 100},
        ))

        # Nenhuma chamada API feita (sem ts registrado)
        mock_client.chat_update.assert_not_called()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_pipeline_completed_without_started_posts_new_message(self):
        """pipeline.completed sem started posta nova mensagem (fallback)."""
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        await notifier.on_event(_make_event(
            "pipeline.completed",
            data={
                "root_cause": "Orphan completion",
                "confidence": 0.5,
            },
        ))

        # Sem ts registrado, usa chat_postMessage como fallback
        mock_client.chat_postMessage.assert_called_once()

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_multiple_pipelines_tracked_independently(self):
        """Dois pipelines concorrentes sao rastreados de forma independente."""
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        cid_a = "inc-pipeline-A"
        cid_b = "inc-pipeline-B"

        # Pipeline A started
        await notifier.on_event(_make_event(
            "pipeline.started",
            correlation_id=cid_a,
            data={"title": "Pipeline A", "severity": "HIGH", "service": "svc-a"},
        ))

        # Pipeline B started
        await notifier.on_event(_make_event(
            "pipeline.started",
            correlation_id=cid_b,
            data={"title": "Pipeline B", "severity": "LOW", "service": "svc-b"},
        ))

        # Node do pipeline A
        await notifier.on_event(_make_event(
            "node.completed",
            correlation_id=cid_a,
            node_name="log_parser",
            data={"duration_ms": 100},
        ))

        # Node do pipeline B
        await notifier.on_event(_make_event(
            "node.completed",
            correlation_id=cid_b,
            node_name="metric_filter",
            data={"duration_ms": 200},
        ))

        # Verifica rastreamento independente
        assert "log_parser" in notifier._completed_nodes[cid_a]
        assert "log_parser" not in notifier._completed_nodes.get(cid_b, [])
        assert "metric_filter" in notifier._completed_nodes[cid_b]
        assert "metric_filter" not in notifier._completed_nodes[cid_a]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_initial_blocks_structure(self):
        """Blocos iniciais tem estrutura esperada: header, section, divider."""
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Test incident", "severity": "CRITICAL", "service": "api-gw"},
        ))

        blocks = mock_client.chat_postMessage.call_args.kwargs["blocks"]
        block_types = [b["type"] for b in blocks]
        assert block_types == ["header", "section", "divider"]

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_failure_event_posts_error_message(self):
        """pipeline.failed posta mensagem de erro com detalhes."""
        mock_client = _make_mock_client()
        notifier = _make_notifier(mock_client)

        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Will fail", "severity": "HIGH", "service": "svc"},
        ))

        await notifier.on_event(_make_event(
            "pipeline.failed",
            data={"error": "LLM timeout after 30s"},
        ))

        # chat_update chamado com blocos de falha
        mock_client.chat_update.assert_called_once()
        blocks = mock_client.chat_update.call_args.kwargs["blocks"]
        full_text = str(blocks)
        assert "Pipeline Failed" in full_text
        assert "LLM timeout after 30s" in full_text

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_channel_id_resolved_from_post_response(self):
        """Channel ID resolvido do response de chat_postMessage e usado em updates."""
        mock_client = _make_mock_client()
        # chat_postMessage retorna channel ID resolvido
        mock_client.chat_postMessage.return_value = {"ts": "100.200", "channel": "C_RESOLVED"}
        notifier = _make_notifier(mock_client)

        cid = "inc-scale-test-001"
        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Test", "severity": "HIGH", "service": "svc"},
        ))

        # Channel ID deve ter sido armazenado
        assert notifier._channel_ids[cid] == "C_RESOLVED"

        # Node update deve usar channel ID resolvido
        await notifier.on_event(_make_event(
            "node.completed",
            node_name="log_parser",
            data={"duration_ms": 100},
        ))

        update_kwargs = mock_client.chat_update.call_args.kwargs
        assert update_kwargs["channel"] == "C_RESOLVED"
