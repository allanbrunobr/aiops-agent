"""
Testes unitários para rag/reranker.py — Stories 8.1 e 8.2.

Cobre fusão de sinais, ordenação por score, limite top_k,
e degradação graciosa do cross-encoder Tier 2.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from rag.reranker import DomainReranker, RankSignals


# ---------------------------------------------------------------------------
# Helpers para criar SearchResult e IndexedChunk falsos
# ---------------------------------------------------------------------------

def _make_chunk(
    source: str = "log",
    text: str = "sample text",
    level: str = "INFO",
    service: str = "unknown-svc",
    deviation: float = 0.0,
    services: list[str] | None = None,
    timestamp: float | None = None,
    weight: float = 0.0,
) -> object:
    """Cria um IndexedChunk falso com atributos mínimos necessários."""
    metadata: dict = {}
    if level:
        metadata["level"] = level
    if service:
        metadata["service"] = service
    if deviation:
        metadata["deviation"] = deviation
    if services is not None:
        metadata["services"] = services
    if timestamp is not None:
        metadata["timestamp"] = timestamp
    if weight:
        metadata["weight"] = weight

    chunk = MagicMock()
    chunk.source = source
    chunk.text = text
    chunk.metadata = metadata
    return chunk


def _make_result(
    source: str = "log",
    text: str = "sample text",
    score: float = 0.5,
    level: str = "INFO",
    service: str = "unknown-svc",
    deviation: float = 0.0,
    services: list[str] | None = None,
    timestamp: float | None = None,
    weight: float = 0.0,
) -> object:
    """Cria um SearchResult falso compatível com DomainReranker."""
    result = MagicMock()
    result.chunk = _make_chunk(
        source=source,
        text=text,
        level=level,
        service=service,
        deviation=deviation,
        services=services,
        timestamp=timestamp,
        weight=weight,
    )
    result.score = score
    return result


# ---------------------------------------------------------------------------
# Story 8.1: Tier 1 — Domain Signal Fusion
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRankSignalsDataclass:
    """Testa estrutura e defaults de RankSignals."""

    def test_default_values_are_zero(self):
        """Todos os campos de RankSignals devem ter default 0.0."""
        signals = RankSignals()
        assert signals.deviation_sigma == 0.0
        assert signals.service_criticality == 0.0
        assert signals.log_level_score == 0.0
        assert signals.vector_score == 0.0
        assert signals.temporal_recency == 0.0
        assert signals.chunk_weight == 0.0

    def test_field_assignment(self):
        """RankSignals deve aceitar valores arbitrários nos campos."""
        signals = RankSignals(
            deviation_sigma=0.6,
            service_criticality=1.0,
            log_level_score=0.3,
            vector_score=0.8,
            temporal_recency=0.5,
            chunk_weight=0.1,
        )
        assert signals.deviation_sigma == 0.6
        assert signals.service_criticality == 1.0


@pytest.mark.unit
class TestFuseScores:
    """Testa que _fuse_scores retorna float entre 0.0 e 1.0."""

    def test_returns_float_between_zero_and_one_all_zeros(self):
        """Score com todos os sinais zero deve ser 0.0."""
        reranker = DomainReranker()
        signals = RankSignals()
        score = reranker._fuse_scores(signals)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0
        assert score == 0.0

    def test_returns_float_between_zero_and_one_all_ones(self):
        """Score com todos os sinais = 1.0 deve ser 1.0."""
        reranker = DomainReranker()
        signals = RankSignals(
            deviation_sigma=1.0,
            service_criticality=1.0,
            log_level_score=1.0,
            vector_score=1.0,
            temporal_recency=1.0,
            chunk_weight=1.0,
        )
        score = reranker._fuse_scores(signals)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0
        assert score == pytest.approx(1.0, abs=1e-9)

    def test_returns_float_between_zero_and_one_mixed(self):
        """Score com sinais mistos deve estar entre 0.0 e 1.0."""
        reranker = DomainReranker()
        signals = RankSignals(
            deviation_sigma=0.6,
            service_criticality=1.0,
            log_level_score=1.0,
            vector_score=0.7,
            temporal_recency=0.8,
            chunk_weight=0.5,
        )
        score = reranker._fuse_scores(signals)
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_weights_sum_to_one(self):
        """Os pesos definidos em _WEIGHTS devem somar 1.0."""
        from rag.reranker import _WEIGHTS
        total = sum(_WEIGHTS.values())
        assert total == pytest.approx(1.0, abs=1e-9)


@pytest.mark.unit
class TestComputeSignals:
    """Testa extração de sinais de domínio de SearchResult."""

    def test_error_level_maps_to_one(self):
        """Nível ERROR deve produzir log_level_score = 1.0."""
        reranker = DomainReranker()
        result = _make_result(level="ERROR")
        signals = reranker._compute_signals(result)
        assert signals.log_level_score == 1.0

    def test_critical_level_maps_to_one(self):
        """Nível CRITICAL deve produzir log_level_score = 1.0."""
        reranker = DomainReranker()
        result = _make_result(level="CRITICAL")
        signals = reranker._compute_signals(result)
        assert signals.log_level_score == 1.0

    def test_warning_level_maps_to_point_six(self):
        """Nível WARNING deve produzir log_level_score = 0.6."""
        reranker = DomainReranker()
        result = _make_result(level="WARNING")
        signals = reranker._compute_signals(result)
        assert signals.log_level_score == pytest.approx(0.6)

    def test_info_level_maps_to_point_three(self):
        """Nível INFO deve produzir log_level_score = 0.3."""
        reranker = DomainReranker()
        result = _make_result(level="INFO")
        signals = reranker._compute_signals(result)
        assert signals.log_level_score == pytest.approx(0.3)

    def test_debug_level_maps_to_point_one(self):
        """Nível DEBUG deve produzir log_level_score = 0.1."""
        reranker = DomainReranker()
        result = _make_result(level="DEBUG")
        signals = reranker._compute_signals(result)
        assert signals.log_level_score == pytest.approx(0.1)

    def test_unknown_level_maps_to_point_two(self):
        """Nível desconhecido deve produzir log_level_score = 0.2."""
        reranker = DomainReranker()
        result = _make_result(level="TRACE")
        signals = reranker._compute_signals(result)
        assert signals.log_level_score == pytest.approx(0.2)

    def test_deviation_sigma_normalization(self):
        """sigma=6 deve produzir deviation_sigma=0.6; sigma=10+ deve ser 1.0."""
        reranker = DomainReranker()

        result_6 = _make_result(deviation=6.0)
        signals_6 = reranker._compute_signals(result_6)
        assert signals_6.deviation_sigma == pytest.approx(0.6)

        result_10 = _make_result(deviation=10.0)
        signals_10 = reranker._compute_signals(result_10)
        assert signals_10.deviation_sigma == pytest.approx(1.0)

        result_15 = _make_result(deviation=15.0)
        signals_15 = reranker._compute_signals(result_15)
        assert signals_15.deviation_sigma == pytest.approx(1.0)

    def test_service_criticality_from_map(self):
        """Serviço no mapa deve usar o valor mapeado."""
        criticality_map = {"payment-svc": 1.0, "debug-svc": 0.1}
        reranker = DomainReranker(service_criticality_map=criticality_map)

        result_payment = _make_result(service="payment-svc")
        signals = reranker._compute_signals(result_payment)
        assert signals.service_criticality == pytest.approx(1.0)

    def test_service_criticality_default_for_unknown(self):
        """Serviço desconhecido deve receber criticidade 0.5."""
        reranker = DomainReranker()
        result = _make_result(service="obscure-svc")
        signals = reranker._compute_signals(result)
        assert signals.service_criticality == pytest.approx(0.5)

    def test_vector_score_clamped_to_range(self):
        """vector_score deve ser clamped para [0.0, 1.0]."""
        reranker = DomainReranker()

        result_high = _make_result(score=1.5)
        signals_high = reranker._compute_signals(result_high)
        assert signals_high.vector_score == pytest.approx(1.0)

        result_neg = _make_result(score=-0.1)
        signals_neg = reranker._compute_signals(result_neg)
        assert signals_neg.vector_score == pytest.approx(0.0)


@pytest.mark.unit
class TestRerank:
    """Testa o método rerank() para ordenação, top_k e comportamento."""

    def test_returns_top_k_results(self):
        """rerank() deve retornar no máximo top_k resultados."""
        reranker = DomainReranker()
        results = [_make_result(score=0.5) for _ in range(20)]
        output = reranker.rerank(results, query="test", top_k=5)
        assert len(output) == 5

    def test_returns_all_when_fewer_than_top_k(self):
        """rerank() deve retornar todos os resultados se houver menos que top_k."""
        reranker = DomainReranker()
        results = [_make_result(score=0.5) for _ in range(3)]
        output = reranker.rerank(results, query="test", top_k=10)
        assert len(output) == 3

    def test_empty_results_returns_empty(self):
        """rerank() com lista vazia deve retornar lista vazia."""
        reranker = DomainReranker()
        output = reranker.rerank([], query="test", top_k=10)
        assert output == []

    def test_sorted_by_fusion_score_descending(self):
        """use_tier2=False deve retornar resultados ordenados por score descendente."""
        criticality_map = {"payment-svc": 1.0, "debug-svc": 0.1}
        reranker = DomainReranker(service_criticality_map=criticality_map)

        # Resultado fraco: INFO, low sigma, debug-svc
        weak = _make_result(
            level="INFO",
            deviation=1.0,
            service="debug-svc",
            score=0.4,
        )
        # Resultado forte: ERROR, high sigma, payment-svc
        strong = _make_result(
            level="ERROR",
            deviation=8.0,
            service="payment-svc",
            score=0.8,
        )

        output = reranker.rerank([weak, strong], query="error investigation", top_k=10, use_tier2=False)
        assert len(output) == 2
        # O resultado forte deve aparecer primeiro
        assert output[0].chunk.metadata["service"] == "payment-svc"
        assert output[1].chunk.metadata["service"] == "debug-svc"

    def test_error_high_sigma_payment_ranks_higher_than_info_low_debug(self):
        """ERROR/high-sigma/payment-svc deve rankear acima de INFO/low-sigma/debug-svc."""
        criticality_map = {"payment-svc": 1.0, "debug-svc": 0.1}
        reranker = DomainReranker(service_criticality_map=criticality_map)

        debug_result = _make_result(
            level="INFO",
            deviation=0.5,
            service="debug-svc",
            score=0.3,
            text="debug info message",
        )
        payment_result = _make_result(
            level="ERROR",
            deviation=7.0,
            service="payment-svc",
            score=0.7,
            text="payment connection error",
        )

        output = reranker.rerank([debug_result, payment_result], query="incident", top_k=10)
        assert output[0].chunk.metadata["service"] == "payment-svc"

    def test_use_tier2_false_returns_tier1_order(self):
        """use_tier2=False deve retornar ordenação Tier 1, sem cross-encoder."""
        criticality_map = {"payment-svc": 1.0}
        reranker = DomainReranker(service_criticality_map=criticality_map, use_cross_encoder=False)

        results = [
            _make_result(level="ERROR", service="payment-svc", deviation=5.0, score=0.9),
            _make_result(level="INFO", service="unknown-svc", deviation=0.1, score=0.3),
        ]
        output = reranker.rerank(results, query="test", top_k=10, use_tier2=False)
        assert output[0].chunk.metadata["service"] == "payment-svc"

    def test_score_written_back_to_result(self):
        """rerank() deve escrever o score de fusão de volta em result.score."""
        reranker = DomainReranker()
        results = [_make_result(score=0.5, level="ERROR", deviation=5.0)]
        output = reranker.rerank(results, query="test", top_k=10)
        # O score deve ter sido substituído pelo score de fusão
        assert 0.0 <= output[0].score <= 1.0


# ---------------------------------------------------------------------------
# Story 8.2: Tier 2 — Cross-Encoder
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCrossEncoderTier2:
    """Testa cross-encoder Tier 2 com mocks."""

    def test_graceful_degradation_on_load_failure(self):
        """Se _load_cross_encoder lançar exceção, use_cross_encoder deve ser False."""
        with patch("rag.reranker.DomainReranker._load_cross_encoder") as mock_load:
            mock_load.side_effect = Exception("Model not found")
            # O __init__ captura a exceção e mantém estado de fallback
            reranker = DomainReranker(use_cross_encoder=True)

        # Com o fallback, use_cross_encoder deve permanecer False
        assert reranker.use_cross_encoder is False
        assert reranker._cross_encoder is None

    def test_cross_encoder_import_failure_disables_tier2(self):
        """Falha no import do sentence_transformers deve deixar use_cross_encoder=False."""
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "sentence_transformers":
                raise ImportError("sentence_transformers not available")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            reranker = DomainReranker(use_cross_encoder=True)

        assert reranker.use_cross_encoder is False
        assert reranker._cross_encoder is None

    def test_tier2_not_triggered_when_results_le_10(self):
        """Tier 2 não deve ser acionado quando len(results) <= 10."""
        mock_ce = MagicMock()
        mock_ce.predict.return_value = [0.8, 0.6]

        reranker = DomainReranker(use_cross_encoder=False)
        reranker._cross_encoder = mock_ce
        reranker.use_cross_encoder = True

        results = [_make_result(score=0.5) for _ in range(5)]
        reranker.rerank(results, query="test", top_k=5, use_tier2=True)

        # Cross-encoder não deve ter sido chamado (len <= 10)
        mock_ce.predict.assert_not_called()

    def test_tier2_produces_blended_scores(self):
        """use_tier2=True com cross-encoder mockado deve produzir scores blended."""
        import numpy as np

        # Cria 12 resultados para ultrapassar o threshold de 10
        results = []
        for i in range(12):
            level = "ERROR" if i < 6 else "INFO"
            results.append(_make_result(level=level, deviation=float(i), score=float(i) / 12))

        mock_ce = MagicMock()
        # Retorna scores para os 6 candidatos top-half (12 // 2 = 6)
        mock_ce.predict.return_value = np.array([0.9, 0.8, 0.7, 0.6, 0.5, 0.4])

        reranker = DomainReranker(use_cross_encoder=False)
        reranker._cross_encoder = mock_ce
        reranker.use_cross_encoder = True

        output = reranker.rerank(results, query="test query", top_k=10, use_tier2=True)

        # Cross-encoder deve ter sido chamado
        mock_ce.predict.assert_called_once()
        # Deve retornar top_k resultados
        assert len(output) == 10
        # Todos os scores devem estar no range [0, 1]
        for r in output:
            assert 0.0 <= r.score <= 1.0

    def test_tier2_blended_formula_correctness(self):
        """Score blended deve seguir fórmula 0.6 × t1 + 0.4 × ce_normalizado.

        Usa 4 pares para que split=2, colocando 2 itens em cada metade.
        CE recebe 2 pares: [1.0, 0.0] — normalizado para [1.0, 0.0].

        r1 (top_half[0]): 0.6 × 0.8 + 0.4 × 1.0 = 0.88
        r2 (top_half[1]): 0.6 × 0.6 + 0.4 × 0.0 = 0.36
        r3 (bottom_half[0]): mantém tier1 = 0.4 (não processado pelo CE)
        r4 (bottom_half[1]): mantém tier1 = 0.2 (não processado pelo CE)
        """
        import numpy as np

        reranker = DomainReranker(use_cross_encoder=False)

        mock_ce = MagicMock()
        # CE scores para 2 itens do top_half
        mock_ce.predict.return_value = np.array([1.0, 0.0])
        reranker._cross_encoder = mock_ce
        reranker.use_cross_encoder = True

        r1 = _make_result(level="ERROR", service="svc-a", deviation=8.0, score=0.9)
        r2 = _make_result(level="WARNING", service="svc-b", deviation=4.0, score=0.6)
        r3 = _make_result(level="INFO", service="svc-c", deviation=2.0, score=0.4)
        r4 = _make_result(level="DEBUG", service="svc-d", deviation=0.5, score=0.2)

        # Tier 1 scores arbitrários para testar a fórmula diretamente
        # split = max(1, 4 // 2) = 2
        tier1_pairs = [(r1, 0.8), (r2, 0.6), (r3, 0.4), (r4, 0.2)]

        blended = reranker._apply_tier2(tier1_pairs, query="test")
        assert len(blended) == 4

        # Ordenado por score decrescente:
        # r1: 0.6×0.8 + 0.4×1.0 = 0.88 (melhor)
        # r3: tier1=0.4 (bottom_half não alterado)
        # r2: 0.6×0.6 + 0.4×0.0 = 0.36
        # r4: tier1=0.2 (bottom_half não alterado)
        scores = [s for _, s in blended]
        assert scores == sorted(scores, reverse=True), "Scores devem estar em ordem decrescente"

        # Verifica a fórmula para os itens do top_half (os que foram processados pelo CE)
        # Encontra r1 e r2 na lista ordenada pelos scores esperados
        expected_r1_score = pytest.approx(0.88, abs=1e-6)
        expected_r2_score = pytest.approx(0.36, abs=1e-6)

        blended_scores = {id(r): s for r, s in blended}
        assert blended_scores[id(r1)] == expected_r1_score
        assert blended_scores[id(r2)] == expected_r2_score
        # r3 e r4 mantêm seus tier1 scores
        assert blended_scores[id(r3)] == pytest.approx(0.4, abs=1e-6)
        assert blended_scores[id(r4)] == pytest.approx(0.2, abs=1e-6)
