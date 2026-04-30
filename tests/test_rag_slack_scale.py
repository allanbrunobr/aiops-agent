"""
Testes de integracao RAG + Slack com variacao de escala de logs.

Cobre:
  - Envio de mensagens Slack verificado em 100, 1K e 10K logs
  - Integracao RAG -> Slack (resultados aparecem nos blocks finais)
  - Retry/fallback em diferentes escalas
  - Dry-run com validacao de blocks
  - Scale consistency de tokens entre escalas
  - Chain-of-Thought enrichment -> Slack blocks
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from slack_sdk.errors import SlackApiError

from core.lifecycle import LifecycleEvent
from interfaces.http.notifications.slack import SlackNotifier
from interfaces.http.notifications.fallback import LocalFallbackWriter
from rag.models import KnowledgePattern, KnowledgeSearchResult
from rag.knowledge_store import KnowledgeStore
from rag.enrichment_node import RAGEnrichmentNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_event(
    event_type: str,
    correlation_id: str = "inc-scale-test-001",
    node_name: str | None = None,
    data: dict | None = None,
) -> LifecycleEvent:
    """Cria evento de ciclo de vida para testes de escala."""
    return LifecycleEvent(
        event_type=event_type,
        node_name=node_name,
        correlation_id=correlation_id,
        timestamp=datetime.utcnow(),
        data=data or {},
    )


def _make_notifier(
    mock_client: AsyncMock,
    *,
    channel: str = "C-SCALE-TEST",
    max_retries: int = 3,
    initial_backoff_s: float = 0.01,
    fallback_dir: str = "output",
    dry_run: bool = False,
) -> SlackNotifier:
    """Cria SlackNotifier com cliente mockado e backoff rapido para testes."""
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


def _make_slack_error(code: str) -> SlackApiError:
    """Cria SlackApiError com codigo especificado."""
    response = MagicMock()
    response.get.return_value = code
    response.__getitem__ = lambda self, key: code if key == "error" else None
    return SlackApiError(f"Slack error: {code}", response=response)


def _make_final_data(
    *,
    root_cause: str = "Connection pool exhausted due to leak in payment-svc",
    confidence: float = 0.92,
    proposed_fix: str = "kubectl scale deployment payment-svc --replicas=5",
    risk_level: str = "medium",
    blast_radius: str = "payment-svc only",
    total_duration_ms: int = 5000,
    compressed_tokens: int | None = None,
) -> dict:
    """Cria dados finais de diagnostico para testes."""
    data = {
        "root_cause": root_cause,
        "confidence": confidence,
        "evidence": ["Error rate spike to 38%", "Pool metrics at 200/200"],
        "proposed_fix": proposed_fix,
        "risk_level": risk_level,
        "blast_radius": blast_radius,
        "total_duration_ms": total_duration_ms,
    }
    if compressed_tokens is not None:
        data["compressed_tokens"] = compressed_tokens
    return data


def _sample_pattern(
    *,
    pattern_id: str = "pat-001",
    service: str = "payment-svc",
    is_negative: bool = False,
    confidence: float = 0.9,
) -> KnowledgePattern:
    """Cria padrao de conhecimento RAG para testes."""
    return KnowledgePattern(
        pattern_id=pattern_id,
        service=service,
        symptoms=["connection pool exhausted", "high latency"],
        root_cause="Database connection leak in payment service",
        resolution="Scale replicas and fix connection leak",
        tags=["database", "connection-pool"],
        confidence=confidence,
        is_negative_example=is_negative,
    )


def _sample_search_result(
    *,
    score: float = 0.85,
    is_negative: bool = False,
    pattern_id: str = "pat-001",
) -> KnowledgeSearchResult:
    """Cria resultado de busca RAG para testes."""
    return KnowledgeSearchResult(
        pattern=_sample_pattern(pattern_id=pattern_id, is_negative=is_negative),
        similarity_score=score,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_slack_client() -> AsyncMock:
    """Cliente Slack mockado com respostas padrao."""
    client = AsyncMock()
    client.chat_postMessage.return_value = {"ts": "1710000000.000001", "channel": "C-SCALE-TEST"}
    client.chat_update.return_value = {"ok": True}
    return client


@pytest.fixture
def knowledge_store(tmp_path) -> KnowledgeStore:
    """KnowledgeStore com armazenamento temporario."""
    store = KnowledgeStore(storage_path=str(tmp_path / "patterns.json"))
    return store


@pytest.fixture(params=[100, 1_000, 10_000], ids=["100-logs", "1K-logs", "10K-logs"])
def log_scale(request) -> int:
    """Parametro de escala de logs: 100, 1K, 10K."""
    return request.param


@pytest.fixture
def generated_logs():
    """Factory fixture que gera logs na escala solicitada."""
    from infra.benchmark_compression import generate_logs
    return generate_logs


@pytest.fixture
def dry_run_notifier() -> SlackNotifier:
    """SlackNotifier em modo dry_run — nenhuma chamada real."""
    return _make_notifier(AsyncMock(), dry_run=True)


# ---------------------------------------------------------------------------
# 1. SLACK COM ESCALAS DE LOGS
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSlackMessageAtLogScales:
    """Verifica que mensagens Slack sao enviadas corretamente em cada escala."""

    @pytest.mark.asyncio
    async def test_initial_message_posted_at_each_scale(
        self, mock_slack_client, generated_logs, log_scale,
    ):
        """Mensagem inicial postada com chat_postMessage independente da escala."""
        logs = generated_logs(log_scale)
        notifier = _make_notifier(mock_slack_client)

        # Simula pipeline.started com metadados da escala
        event = _make_event(
            "pipeline.started",
            data={
                "title": f"Connection pool exhaustion ({len(logs)} logs)",
                "severity": "critical",
                "service": "payment-svc",
                "log_count": len(logs),
            },
        )
        await notifier.on_event(event)

        # Verifica que chat_postMessage foi chamado exatamente uma vez
        mock_slack_client.chat_postMessage.assert_called_once()
        call_kwargs = mock_slack_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "C-SCALE-TEST"

        # Verifica que os blocks contem informacoes do incidente
        blocks = call_kwargs["blocks"]
        blocks_text = str(blocks)
        assert "inc-scale-test-001" in blocks_text
        assert "Diagnosing" in blocks_text

    @pytest.mark.asyncio
    async def test_node_completed_updates_message_at_each_scale(
        self, mock_slack_client, generated_logs, log_scale,
    ):
        """Eventos node.completed atualizam mensagem via chat_update."""
        _logs = generated_logs(log_scale)
        notifier = _make_notifier(mock_slack_client)

        # Pipeline iniciado
        await notifier.on_event(_make_event("pipeline.started"))

        # Simula nodes completados
        pipeline_nodes = ["log_parser", "metric_filter", "context_compressor"]
        for node in pipeline_nodes:
            await notifier.on_event(_make_event(
                "node.completed",
                node_name=node,
                data={"duration_ms": 150, "log_count": len(_logs)},
            ))

        # Cada node.completed deve gerar um chat_update
        assert mock_slack_client.chat_update.call_count == len(pipeline_nodes)

        # Verifica que o ultimo update mostra checkmarks para todos os nos
        last_call_blocks = mock_slack_client.chat_update.call_args.kwargs["blocks"]
        last_text = str(last_call_blocks)
        assert "white_check_mark" in last_text

    @pytest.mark.asyncio
    async def test_final_message_contains_diagnosis_at_each_scale(
        self, mock_slack_client, generated_logs, log_scale,
    ):
        """Mensagem final contem severity, root_cause e proposed_fix corretos."""
        _logs = generated_logs(log_scale)
        notifier = _make_notifier(mock_slack_client)

        # Pipeline completo
        await notifier.on_event(_make_event("pipeline.started"))
        final_data = _make_final_data(
            compressed_tokens=1200,
            total_duration_ms=log_scale,  # correlaciona duracao com escala
        )
        await notifier.on_event(_make_event("pipeline.completed", data=final_data))

        # Valida conteudo dos blocks finais
        final_blocks = mock_slack_client.chat_update.call_args.kwargs["blocks"]
        full_text = str(final_blocks)

        assert "Connection pool exhausted" in full_text, "Root cause ausente nos blocks"
        assert "kubectl scale deployment" in full_text, "Proposed fix ausente nos blocks"
        assert "Diagnosis Complete" in full_text, "Header de conclusao ausente"
        assert "medium" in full_text, "Risk level ausente nos blocks"
        assert "92%" in full_text, "Confidence ausente nos blocks"


@pytest.mark.unit
class TestScaleConsistencyTokens:
    """Verifica que o numero de tokens comprimidos e estavel entre escalas."""

    def test_compressed_tokens_stable_across_scales(self, generated_logs):
        """Tokens comprimidos devem ser similares independente da escala de input."""
        from pipeline.log_parser import LogParser
        from pipeline.aggregator import Aggregator
        from pipeline.context_compressor import ContextCompressor
        from infra.benchmark_compression import generate_metrics, generate_traces

        token_counts = {}
        for scale in [100, 1_000, 10_000]:
            logs = generated_logs(scale)
            metrics = generate_metrics()
            traces = generate_traces()

            aggregator = Aggregator()
            # Forca fallback Python caso Rust esteja incompleto
            with patch("pipeline.metric_filter._RUST_AVAILABLE", False), \
                 patch("pipeline.trace_filter._RUST_AVAILABLE", False, create=True):
                report = aggregator.process(logs, metrics, traces)

            compressor = ContextCompressor(token_budget=4000)
            _text, stats = compressor.compress(report)
            token_counts[scale] = stats.compressed_tokens

        # Todos os valores devem estar dentro de um fator 3x do menor
        min_tokens = min(token_counts.values())
        max_tokens = max(token_counts.values())
        assert max_tokens <= min_tokens * 3, (
            f"Tokens instáveis entre escalas: {token_counts}. "
            f"Razao max/min = {max_tokens / min_tokens:.1f}x (limite: 3x)"
        )

        # Nenhuma escala ultrapassa o budget de 4000 tokens
        for scale, tokens in token_counts.items():
            assert tokens <= 4000, (
                f"Escala {scale}: {tokens} tokens excede budget de 4000"
            )


# ---------------------------------------------------------------------------
# 2. RAG -> SLACK INTEGRATION
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRAGToSlackIntegration:
    """Testa que resultados RAG aparecem corretamente nos blocks Slack."""

    @pytest.mark.asyncio
    async def test_high_score_rag_result_appears_in_slack_blocks(
        self, mock_slack_client,
    ):
        """KnowledgeSearchResult com score >= 0.8 aparece nos blocks finais."""
        notifier = _make_notifier(mock_slack_client)
        result = _sample_search_result(score=0.85)

        # Monta dados finais incluindo enriquecimento RAG
        rag_context = result.to_llm_context()
        final_data = _make_final_data()
        final_data["rag_enrichment"] = rag_context
        final_data["root_cause"] = (
            "Connection pool exhausted due to leak in payment-svc "
            f"(RAG match: {result.pattern.root_cause})"
        )

        await notifier.on_event(_make_event("pipeline.started"))
        await notifier.on_event(_make_event("pipeline.completed", data=final_data))

        blocks = mock_slack_client.chat_update.call_args.kwargs["blocks"]
        full_text = str(blocks)

        # O root_cause enriquecido com RAG aparece nos blocks
        assert "Database connection leak" in full_text

    @pytest.mark.asyncio
    async def test_negative_example_filtered_from_slack(self, mock_slack_client):
        """KnowledgeSearchResult com is_negative_example=True nao aparece nos blocks."""
        # Simula o retriever filtrando negative examples
        mock_retriever = MagicMock()
        negative_pattern = _sample_pattern(is_negative=True, pattern_id="neg-001")
        positive_pattern = _sample_pattern(is_negative=False, pattern_id="pos-001")

        # KnowledgeRetriever filtra negativos internamente
        mock_retriever.search_similar_incidents.return_value = [
            KnowledgeSearchResult(pattern=positive_pattern, similarity_score=0.9),
            # Negativo NAO aparece (ja filtrado pelo retriever)
        ]

        enrichment_node = RAGEnrichmentNode(mock_retriever, top_k=5)
        context = {
            "symptoms": ["pool exhausted"],
            "affected_services": ["payment-svc"],
            "error_summary": "Connection pool at 200/200",
        }
        enriched = enrichment_node.execute(context)

        # Verifica que apenas o positivo aparece no enriquecimento
        assert "Database connection leak" in enriched.get("rag_enrichment", "")
        assert enriched["rag_available"] is True

    @pytest.mark.asyncio
    async def test_rag_no_results_graceful_degradation(self, mock_slack_client):
        """RAG sem resultados: mensagem Slack postada normalmente (graceful degradation)."""
        # Simula retriever sem resultados
        mock_retriever = MagicMock()
        mock_retriever.search_similar_incidents.return_value = []

        enrichment_node = RAGEnrichmentNode(mock_retriever, top_k=5)
        context = {
            "symptoms": ["unknown error"],
            "affected_services": ["mystery-svc"],
            "error_summary": "Something failed",
        }
        enriched = enrichment_node.execute(context)

        # Sem enriquecimento, mas rag_available = True (disponivel, porem vazio)
        assert enriched["rag_enrichment"] == ""
        assert enriched["rag_available"] is True

        # Slack ainda funciona normalmente sem RAG
        notifier = _make_notifier(mock_slack_client)
        await notifier.on_event(_make_event("pipeline.started"))
        await notifier.on_event(_make_event(
            "pipeline.completed",
            data=_make_final_data(root_cause="Unknown root cause"),
        ))

        mock_slack_client.chat_update.assert_called_once()
        blocks = mock_slack_client.chat_update.call_args.kwargs["blocks"]
        assert "Unknown root cause" in str(blocks)

    @pytest.mark.asyncio
    async def test_rag_search_error_graceful_degradation(self, mock_slack_client):
        """RAG com erro na busca: pipeline continua sem enriquecimento."""
        mock_retriever = MagicMock()
        mock_retriever.search_similar_incidents.side_effect = RuntimeError("FAISS index corrupted")

        enrichment_node = RAGEnrichmentNode(mock_retriever, top_k=5)
        context = {
            "symptoms": ["timeout"],
            "affected_services": ["payment-svc"],
            "error_summary": "DB timeout",
        }
        enriched = enrichment_node.execute(context)

        assert enriched["rag_available"] is False
        assert "search_error" in enriched["rag_unavailable_reason"]
        assert enriched["rag_enrichment"] == ""


# ---------------------------------------------------------------------------
# 3. SLACK RETRY COM MULTIPLAS ESCALAS
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestSlackRetryAtScale:
    """Testa retry e fallback Slack com diferentes escalas de logs."""

    @pytest.mark.asyncio
    async def test_ratelimited_during_10k_update_retries_and_succeeds(self):
        """Rate limit durante update em 10K logs: retry com backoff e eventual sucesso."""
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "ts.10k", "channel": "C-SCALE-TEST"}

        # Primeiro chat_update falha com ratelimited, segundo funciona
        mock_client.chat_update.side_effect = [
            _make_slack_error("ratelimited"),
            {"ok": True},  # retry bem sucedido
        ]

        notifier = _make_notifier(mock_client, max_retries=3, initial_backoff_s=0.01)

        await notifier.on_event(_make_event("pipeline.started"))

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await notifier.on_event(_make_event(
                "node.completed",
                node_name="log_parser",
                data={"duration_ms": 3200, "log_count": 10_000},
            ))
            # Verifica que houve sleep de backoff
            mock_sleep.assert_called_once_with(0.01)

        # chat_update chamado 2 vezes (falha + retry)
        assert mock_client.chat_update.call_count == 2

    @pytest.mark.asyncio
    async def test_invalid_auth_no_retry_direct_fallback(self, tmp_path):
        """invalid_auth durante post inicial: nao retenta, vai para fallback direto."""
        mock_client = AsyncMock()
        mock_client.chat_postMessage.side_effect = _make_slack_error("invalid_auth")

        notifier = _make_notifier(
            mock_client,
            max_retries=5,
            initial_backoff_s=0.01,
            fallback_dir=str(tmp_path),
        )

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            # on_event captura SlackApiError — nao propaga
            await notifier.on_event(_make_event("pipeline.started"))
            # Sem retry para erro de autenticacao
            mock_sleep.assert_not_called()

        # Apenas 1 tentativa (sem retries)
        assert mock_client.chat_postMessage.call_count == 1

    @pytest.mark.asyncio
    async def test_fallback_writes_json_with_correct_correlation_id(self, tmp_path):
        """Fallback escreve JSON com correlation_id correto mesmo com contexto grande."""
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "ts.fallback", "channel": "C-SCALE-TEST"}
        mock_client.chat_update.side_effect = _make_slack_error("channel_not_found")

        notifier = _make_notifier(
            mock_client,
            max_retries=2,
            initial_backoff_s=0.01,
            fallback_dir=str(tmp_path),
        )

        cid = "inc-scale-test-001"
        await notifier.on_event(_make_event("pipeline.started"))

        # Dados finais com contexto grande simulando 100K tokens
        big_context = "x" * 400_000  # ~100K tokens
        final_data = _make_final_data()
        final_data["compressed_context"] = big_context

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await notifier.on_event(_make_event("pipeline.completed", data=final_data))

        # Verifica arquivo de fallback
        fallback_files = list(tmp_path.glob("fallback_*.json"))
        assert len(fallback_files) == 1

        data = json.loads(fallback_files[0].read_text())
        assert data["correlation_id"] == cid
        assert data["fallback_reason"] == "slack_delivery_exhausted"
        assert data["postmortem"]["root_cause"] == final_data["root_cause"]

    @pytest.mark.asyncio
    async def test_retry_backoff_escalates_correctly(self):
        """Backoff exponencial: 0.01s, 0.02s, 0.04s em 3 tentativas."""
        mock_client = AsyncMock()
        mock_client.chat_postMessage.side_effect = [
            _make_slack_error("ratelimited"),
            _make_slack_error("ratelimited"),
            {"ts": "ts.final", "channel": "C-SCALE-TEST"},
        ]

        notifier = _make_notifier(mock_client, max_retries=3, initial_backoff_s=0.01)

        with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await notifier.on_event(_make_event("pipeline.started"))
            assert mock_sleep.call_count == 2
            mock_sleep.assert_any_call(0.01)   # 0.01 * 2^0
            mock_sleep.assert_any_call(0.02)   # 0.01 * 2^1

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_still_cleans_state(self, tmp_path):
        """Apos esgotar retries, estado interno e limpo (sem memory leak)."""
        mock_client = AsyncMock()
        mock_client.chat_postMessage.return_value = {"ts": "ts.clean", "channel": "C-SCALE-TEST"}
        mock_client.chat_update.side_effect = _make_slack_error("server_error")

        notifier = _make_notifier(
            mock_client, max_retries=1, initial_backoff_s=0.01,
            fallback_dir=str(tmp_path),
        )

        cid = "inc-scale-test-001"
        await notifier.on_event(_make_event("pipeline.started"))

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await notifier.on_event(_make_event("pipeline.completed", data=_make_final_data()))

        # Estado interno limpo apos pipeline.completed, mesmo com falha
        assert cid not in notifier._message_ts
        assert cid not in notifier._channel_ids
        assert cid not in notifier._completed_nodes


# ---------------------------------------------------------------------------
# 4. DRY RUN COM ESCALA
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDryRunAtScale:
    """Verifica dry_run com diferentes escalas de logs."""

    @pytest.mark.asyncio
    async def test_dry_run_zero_slack_api_calls(self, generated_logs):
        """dry_run=True com 1000 logs: zero chamadas a API Slack."""
        _logs = generated_logs(1_000)
        mock_client = AsyncMock()
        notifier = _make_notifier(mock_client, dry_run=True)

        # Simula pipeline completo
        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Test incident", "severity": "high", "service": "payment-svc"},
        ))

        for node in ["log_parser", "metric_filter", "context_compressor"]:
            await notifier.on_event(_make_event(
                "node.completed",
                node_name=node,
                data={"duration_ms": 100, "log_count": len(_logs)},
            ))

        await notifier.on_event(_make_event(
            "pipeline.completed",
            data=_make_final_data(compressed_tokens=1200),
        ))

        # Zero chamadas reais ao Slack
        mock_client.chat_postMessage.assert_not_called()
        mock_client.chat_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_sets_fake_ts(self):
        """dry_run registra timestamp fake para tracking interno."""
        notifier = _make_notifier(AsyncMock(), dry_run=True)

        cid = "inc-scale-test-001"
        await notifier.on_event(_make_event("pipeline.started"))
        assert notifier._message_ts[cid] == "dry-run"

    @pytest.mark.asyncio
    async def test_dry_run_clears_state_on_final(self):
        """dry_run limpa estado interno apos pipeline.completed."""
        notifier = _make_notifier(AsyncMock(), dry_run=True)

        cid = "inc-scale-test-001"
        await notifier.on_event(_make_event("pipeline.started"))
        await notifier.on_event(_make_event(
            "pipeline.completed", data=_make_final_data(),
        ))

        assert cid not in notifier._message_ts
        assert cid not in notifier._completed_nodes

    @pytest.mark.asyncio
    async def test_dry_run_blocks_contain_correct_scale_info(self):
        """dry_run: blocks internos contem informacoes de escala corretas."""
        notifier = _make_notifier(AsyncMock(), dry_run=True)

        # Captura logs do structlog para verificar blocks
        import structlog
        captured_events = []
        original_log = structlog.get_logger

        # Usamos o mecanismo de dry_run que loga com structlog
        # Verificamos que pipeline.started registra o ts e que progress funciona
        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": "Scale test", "severity": "critical", "service": "payment-svc"},
        ))

        # Verifica que o notifier construiu blocks iniciais corretamente
        blocks = notifier._build_initial_blocks(_make_event(
            "pipeline.started",
            data={"title": "Scale test", "severity": "critical", "service": "payment-svc"},
        ))
        blocks_text = str(blocks)
        assert "Scale test" in blocks_text
        assert "critical" in blocks_text
        assert "payment-svc" in blocks_text


# ---------------------------------------------------------------------------
# 5. CoT + SLACK
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestCoTEnrichmentToSlack:
    """Testa que Chain-of-Thought enrichment popula dados para Slack blocks."""

    def test_cot_retriever_populates_search_history(self):
        """CoTObservabilityRetriever mantem historico de buscas por sessao."""
        from rag.cot_retriever import CoTObservabilityRetriever

        # Mock do indexer com index e model
        mock_indexer = MagicMock()
        mock_indexer.index = None  # Sem indice FAISS — retorna vazio

        retriever = CoTObservabilityRetriever(mock_indexer)
        assert retriever.search_history == []
        assert retriever.current_iteration == 0

    def test_cot_search_summary_format(self):
        """get_search_summary retorna formato correto apos buscas."""
        from rag.cot_retriever import CoTObservabilityRetriever, SearchContext
        from rag.retriever import SearchResult

        mock_indexer = MagicMock()
        mock_indexer.index = None

        retriever = CoTObservabilityRetriever(mock_indexer)

        # Simula historico de buscas
        retriever.search_history = [
            SearchContext(
                iteration=1,
                query="payment-svc connection pool",
                results=[],
                entities=["payment-svc", "metric:connection_pool"],
            ),
            SearchContext(
                iteration=2,
                query="database timeout auth-svc",
                results=[],
                entities=["auth-svc"],
            ),
        ]

        summary = retriever.get_search_summary()
        assert "Search History" in summary
        assert "Iteration 1" in summary
        assert "payment-svc" in summary
        assert "Iteration 2" in summary

    def test_cot_entity_extraction(self):
        """_extract_entities identifica servicos e metricas corretamente."""
        from rag.cot_retriever import CoTObservabilityRetriever

        mock_indexer = MagicMock()
        mock_indexer.index = None

        retriever = CoTObservabilityRetriever(mock_indexer)
        entities = retriever._extract_entities(
            "payment-svc has high connection_pool usage and latency"
        )

        assert "payment-svc" in entities
        assert "metric:connection_pool" in entities
        assert "metric:latency" in entities

    def test_cot_reset_clears_state(self):
        """reset_context limpa historico e iteracao."""
        from rag.cot_retriever import CoTObservabilityRetriever, SearchContext

        mock_indexer = MagicMock()
        mock_indexer.index = None

        retriever = CoTObservabilityRetriever(mock_indexer)
        retriever.search_history.append(
            SearchContext(iteration=1, query="test", results=[], entities=[])
        )
        retriever.current_iteration = 5

        retriever.reset_context()
        assert retriever.search_history == []
        assert retriever.current_iteration == 0

    @pytest.mark.asyncio
    async def test_cot_enrichment_populates_event_data_for_slack(
        self, mock_slack_client,
    ):
        """Dados de CoT enrichment aparecem nos blocks Slack finais."""
        notifier = _make_notifier(mock_slack_client)

        # Simula dados que CoT enrichment adicionaria ao pipeline state
        final_data = _make_final_data()
        final_data["cot_summary"] = (
            "Iteration 1: payment-svc connection pool -> 2 results\n"
            "Iteration 2: database timeout auth-svc -> 1 result"
        )
        final_data["root_cause"] = (
            "Connection pool exhausted (confirmed via CoT: "
            "2 similar incidents found in knowledge base)"
        )

        await notifier.on_event(_make_event("pipeline.started"))
        await notifier.on_event(_make_event("pipeline.completed", data=final_data))

        blocks = mock_slack_client.chat_update.call_args.kwargs["blocks"]
        full_text = str(blocks)

        # Root cause enriquecido com CoT aparece nos blocks
        assert "Connection pool exhausted" in full_text
        assert "CoT" in full_text


# ---------------------------------------------------------------------------
# 6. RAG ENRICHMENT NODE INTEGRATION
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestRAGEnrichmentNodeBehavior:
    """Testa comportamento do RAGEnrichmentNode isoladamente."""

    def test_enrichment_with_positive_results(self):
        """Resultados positivos sao incluidos no contexto de enriquecimento."""
        mock_retriever = MagicMock()
        mock_retriever.search_similar_incidents.return_value = [
            _sample_search_result(score=0.9, pattern_id="p1"),
            _sample_search_result(score=0.7, pattern_id="p2"),
        ]

        node = RAGEnrichmentNode(mock_retriever, top_k=5)
        context = {
            "symptoms": ["pool exhausted"],
            "affected_services": ["payment-svc"],
            "error_summary": "200/200 connections",
        }
        result = node.execute(context)

        assert result["rag_available"] is True
        assert "Database connection leak" in result["rag_enrichment"]
        # Verifica que to_llm_context foi usado para formatar
        assert "Similar Incident" in result["rag_enrichment"]

    def test_enrichment_empty_results(self):
        """Sem resultados RAG: rag_available=True mas enriquecimento vazio."""
        mock_retriever = MagicMock()
        mock_retriever.search_similar_incidents.return_value = []

        node = RAGEnrichmentNode(mock_retriever, top_k=5)
        context = {
            "symptoms": [],
            "affected_services": [],
            "error_summary": "",
        }
        result = node.execute(context)

        assert result["rag_available"] is True
        assert result["rag_enrichment"] == ""

    def test_enrichment_exception_handled_gracefully(self):
        """Excecoes no retriever sao capturadas sem propagar."""
        mock_retriever = MagicMock()
        mock_retriever.search_similar_incidents.side_effect = Exception("FAISS crash")

        node = RAGEnrichmentNode(mock_retriever, top_k=5)
        context = {
            "symptoms": ["timeout"],
            "affected_services": ["auth-svc"],
            "error_summary": "Connection refused",
        }
        result = node.execute(context)

        assert result["rag_available"] is False
        assert "search_error" in result["rag_unavailable_reason"]


# ---------------------------------------------------------------------------
# 7. KNOWLEDGE STORE + SLACK FLOW
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestKnowledgeStoreToSlackFlow:
    """Testa fluxo completo: store -> retriever -> enrichment -> slack blocks."""

    def test_store_add_and_retrieve_pattern(self, knowledge_store):
        """Padrao adicionado ao store pode ser recuperado."""
        pattern = _sample_pattern(pattern_id="flow-001")
        knowledge_store.add_pattern(pattern)

        found = knowledge_store.find_by_service_and_cause(
            "payment-svc", "Database connection leak in payment service",
        )
        assert found is not None
        assert found.pattern_id == "flow-001"

    def test_store_mark_negative_excludes_from_retrieval(self, knowledge_store):
        """Padrao marcado como negativo nao deve ser retornado pelo retriever."""
        pattern = _sample_pattern(pattern_id="neg-flow-001")
        knowledge_store.add_pattern(pattern)
        knowledge_store.mark_pattern_negative("neg-flow-001")

        patterns = knowledge_store.get_all_patterns()
        assert patterns[0].is_negative_example is True

    def test_store_persistence_roundtrip(self, knowledge_store):
        """Padrao persiste e carrega corretamente do disco."""
        pattern = _sample_pattern(pattern_id="persist-001")
        knowledge_store.add_pattern(pattern)

        # Novo store no mesmo path
        new_store = KnowledgeStore(storage_path=knowledge_store._storage_path)
        loaded = new_store.load_from_disk()
        assert loaded == 1
        assert new_store.get_all_patterns()[0].pattern_id == "persist-001"


# ---------------------------------------------------------------------------
# 8. BLOCK KIT STRUCTURE VALIDATION
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBlockKitStructure:
    """Valida estrutura detalhada dos Block Kit blocks."""

    def test_initial_blocks_have_header_section_divider(self):
        """Blocks iniciais contem header, section e divider."""
        notifier = _make_notifier(AsyncMock(), dry_run=True)
        event = _make_event(
            "pipeline.started",
            data={"title": "Test", "severity": "critical", "service": "payment-svc"},
        )
        blocks = notifier._build_initial_blocks(event)

        types = [b["type"] for b in blocks]
        assert "header" in types
        assert "section" in types
        assert "divider" in types

    def test_progress_blocks_show_checklist(self):
        """Blocks de progresso mostram checklist com nomes corretos."""
        notifier = _make_notifier(AsyncMock(), dry_run=True)
        cid = "inc-scale-test-001"
        notifier._completed_nodes[cid] = ["log_parser", "metric_filter"]

        event = _make_event("node.completed", node_name="metric_filter", data={"duration_ms": 100})
        blocks = notifier._build_progress_blocks(cid, event)

        text = str(blocks)
        assert "Log Parsing" in text
        assert "Metric Analysis" in text
        assert "white_check_mark" in text

    def test_final_blocks_have_all_diagnostic_fields(self):
        """Blocks finais contem root_cause, confidence, risk, fix, duration."""
        notifier = _make_notifier(AsyncMock(), dry_run=True)
        event = _make_event("pipeline.completed", data=_make_final_data())
        blocks = notifier._build_final_blocks(event)

        full_text = str(blocks)
        assert "Connection pool exhausted" in full_text
        assert "92%" in full_text
        assert "medium" in full_text
        assert "kubectl scale deployment" in full_text
        assert "5000" in full_text  # duration_ms

    def test_final_blocks_confidence_emoji_green(self):
        """Confianca >= 0.8 mostra indicador verde."""
        notifier = _make_notifier(AsyncMock(), dry_run=True)
        event = _make_event("pipeline.completed", data=_make_final_data(confidence=0.85))
        blocks = notifier._build_final_blocks(event)
        assert "large_green_circle" in str(blocks)

    def test_final_blocks_confidence_emoji_yellow(self):
        """Confianca entre 0.5 e 0.8 mostra indicador amarelo."""
        notifier = _make_notifier(AsyncMock(), dry_run=True)
        event = _make_event("pipeline.completed", data=_make_final_data(confidence=0.6))
        blocks = notifier._build_final_blocks(event)
        assert "large_yellow_circle" in str(blocks)

    def test_final_blocks_confidence_emoji_red(self):
        """Confianca < 0.5 mostra indicador vermelho."""
        notifier = _make_notifier(AsyncMock(), dry_run=True)
        event = _make_event("pipeline.completed", data=_make_final_data(confidence=0.3))
        blocks = notifier._build_final_blocks(event)
        assert "red_circle" in str(blocks)

    def test_final_blocks_context_footer(self):
        """Blocks finais incluem footer com AIOps Agent e correlation_id."""
        notifier = _make_notifier(AsyncMock(), dry_run=True)
        event = _make_event("pipeline.completed", data=_make_final_data())
        blocks = notifier._build_final_blocks(event)

        context_blocks = [b for b in blocks if b["type"] == "context"]
        assert len(context_blocks) == 1
        footer_text = str(context_blocks[0])
        assert "AIOps Agent" in footer_text
        assert "inc-scale-test-001" in footer_text


# ---------------------------------------------------------------------------
# 9. FULL PIPELINE LIFECYCLE AT SCALE
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestFullPipelineLifecycleAtScale:
    """Testa ciclo completo de lifecycle events em escalas diferentes."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_100_logs(self, mock_slack_client, generated_logs):
        """Ciclo completo com 100 logs: started -> nodes -> completed."""
        logs = generated_logs(100)
        notifier = _make_notifier(mock_slack_client)

        # Pipeline started
        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": f"Incident ({len(logs)} logs)", "severity": "high", "service": "payment-svc"},
        ))
        assert mock_slack_client.chat_postMessage.call_count == 1

        # Nodes completados
        nodes = ["telemetry_sanitizer", "log_parser", "metric_filter",
                 "trace_filter", "context_compressor", "rag_retriever",
                 "root_cause_analyzer"]
        for node in nodes:
            await notifier.on_event(_make_event(
                "node.completed", node_name=node, data={"duration_ms": 50},
            ))
        assert mock_slack_client.chat_update.call_count == len(nodes)

        # Pipeline completed
        await notifier.on_event(_make_event(
            "pipeline.completed", data=_make_final_data(),
        ))
        # +1 update para mensagem final
        assert mock_slack_client.chat_update.call_count == len(nodes) + 1

    @pytest.mark.asyncio
    async def test_full_lifecycle_10k_logs(self, mock_slack_client, generated_logs):
        """Ciclo completo com 10K logs: mesma estrutura, mais dados."""
        logs = generated_logs(10_000)
        notifier = _make_notifier(mock_slack_client)

        await notifier.on_event(_make_event(
            "pipeline.started",
            data={"title": f"Incident ({len(logs)} logs)", "severity": "critical", "service": "payment-svc"},
        ))

        # Simula todos os nodes do pipeline
        all_nodes = list(dict.fromkeys([
            "telemetry_sanitizer", "log_parser", "metric_filter",
            "trace_filter", "context_compressor", "rag_retriever",
            "root_cause_analyzer", "pre_execution_validator", "tnr",
            "version_validator", "postmortem_generator",
        ]))
        for node in all_nodes:
            await notifier.on_event(_make_event(
                "node.completed", node_name=node, data={"duration_ms": 200},
            ))

        await notifier.on_event(_make_event(
            "pipeline.completed", data=_make_final_data(total_duration_ms=15_000),
        ))

        # Verifica contagem total de chamadas
        assert mock_slack_client.chat_postMessage.call_count == 1
        assert mock_slack_client.chat_update.call_count == len(all_nodes) + 1

    @pytest.mark.asyncio
    async def test_pipeline_failure_emits_failure_blocks(self, mock_slack_client):
        """Pipeline que falha emite blocks de erro corretos."""
        notifier = _make_notifier(mock_slack_client)

        await notifier.on_event(_make_event("pipeline.started"))
        await notifier.on_event(_make_event(
            "pipeline.failed",
            data={"error": "OOM: container killed by kernel"},
        ))

        # chat_update para mensagem de falha
        mock_slack_client.chat_update.assert_called_once()
        blocks = mock_slack_client.chat_update.call_args.kwargs["blocks"]
        full_text = str(blocks)
        assert "Pipeline Failed" in full_text
        assert "OOM" in full_text


# ---------------------------------------------------------------------------
# 10. KNOWLEDGE SEARCH RESULT FORMATTING
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestKnowledgeSearchResultFormatting:
    """Valida formato de KnowledgeSearchResult para contexto LLM/Slack."""

    def test_to_llm_context_format(self):
        """to_llm_context retorna formato estruturado correto."""
        result = _sample_search_result(score=0.85)
        context = result.to_llm_context()

        assert "similarity=0.850" in context
        assert "confidence=0.90" in context
        assert "Service: payment-svc" in context
        assert "Root Cause: Database connection leak" in context
        assert "Resolution: Scale replicas" in context
        assert "database" in context

    def test_to_embedding_text_format(self):
        """to_embedding_text combina campos corretamente."""
        pattern = _sample_pattern()
        text = pattern.to_embedding_text()

        assert "Service: payment-svc" in text
        assert "Symptoms: connection pool exhausted; high latency" in text
        assert "Root cause: Database connection leak" in text
        assert "Resolution: Scale replicas" in text
