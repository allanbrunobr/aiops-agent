"""
Reranker de domínio para resultados de busca semântica em observabilidade.

Combina sinais de domínio (desvio sigma, criticidade do serviço, nível de log,
score vetorial, recência temporal e peso do chunk) para reordenar os candidatos
retornados pelo FAISS antes de entregá-los ao agente de diagnóstico.

Suporta modo Tier 2 opcional com cross-encoder para refinamento adicional.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field


@dataclass
class RankSignals:
    """Sinais de domínio extraídos de um SearchResult para fusão de scores."""

    deviation_sigma: float = 0.0
    service_criticality: float = 0.0
    log_level_score: float = 0.0
    vector_score: float = 0.0
    temporal_recency: float = 0.0
    chunk_weight: float = 0.0


# Mapeamento de nível de log para score normalizado
_LOG_LEVEL_SCORES: dict[str, float] = {
    "CRITICAL": 1.0,
    "ERROR": 1.0,
    "WARNING": 0.6,
    "WARN": 0.6,
    "INFO": 0.3,
    "DEBUG": 0.1,
}

# Pesos da fusão — soma = 1.0
_WEIGHTS = {
    "deviation_sigma": 0.25,
    "service_criticality": 0.25,
    "log_level": 0.20,
    "vector_score": 0.15,
    "temporal_recency": 0.10,
    "chunk_weight": 0.05,
}


class DomainReranker:
    """Reranker baseado em sinais de domínio para busca em observabilidade.

    Combina múltiplos sinais extraídos dos metadados dos chunks para produzir
    um score de relevância mais preciso do que o score vetorial puro do FAISS.

    Opcionalmente, pode usar um cross-encoder (Tier 2) para refinamento adicional
    nos candidatos mais promissores.
    """

    def __init__(
        self,
        service_criticality_map: dict[str, float] | None = None,
        use_cross_encoder: bool = False,
    ) -> None:
        """Inicializa o reranker com mapa de criticidade e flag de cross-encoder.

        Args:
            service_criticality_map: Mapeamento de nome de serviço para score
                de criticidade (0.0–1.0). Serviços desconhecidos recebem 0.5.
            use_cross_encoder: Se True, tenta carregar o modelo cross-encoder
                para refinamento Tier 2. Em caso de falha no carregamento,
                degradação silenciosa para Tier 1 apenas.
        """
        self.service_criticality_map: dict[str, float] = service_criticality_map or {}
        self.use_cross_encoder: bool = False
        self._cross_encoder = None

        if use_cross_encoder:
            try:
                self._load_cross_encoder()
            except Exception as exc:
                warnings.warn(
                    f'Falha ao inicializar cross-encoder — Tier 2 desabilitado: {exc}',
                    RuntimeWarning,
                    stacklevel=2,
                )

    def _load_cross_encoder(self) -> None:
        """Carrega o modelo cross-encoder de forma lazy com fallback silencioso."""
        try:
            from sentence_transformers import CrossEncoder  # type: ignore[import]

            self._cross_encoder = CrossEncoder("cross-encoder/qnli-distilroberta-base")
            self.use_cross_encoder = True
        except Exception as exc:
            warnings.warn(
                f"Falha ao carregar cross-encoder — Tier 2 desabilitado: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
            self.use_cross_encoder = False
            self._cross_encoder = None

    def _compute_signals(self, result: object) -> RankSignals:
        """Extrai sinais de domínio de um SearchResult.

        Args:
            result: Instância de SearchResult com atributos .chunk e .score.

        Returns:
            RankSignals com todos os sinais normalizados para [0.0, 1.0].
        """
        signals = RankSignals()

        chunk = result.chunk  # type: ignore[attr-defined]
        metadata: dict = chunk.metadata if hasattr(chunk, "metadata") else {}

        # --- deviation_sigma: min(sigma / 10.0, 1.0) ---
        raw_sigma = float(metadata.get("deviation", 0.0))
        signals.deviation_sigma = min(raw_sigma / 10.0, 1.0)

        # --- service_criticality: lookup com default 0.5 ---
        service = metadata.get("service", "")
        if not service:
            # Tenta extrair serviços de lista (chunks de log)
            services = metadata.get("services", [])
            if services:
                # Usa a maior criticidade entre os serviços do chunk
                service = max(services, key=lambda s: self.service_criticality_map.get(s, 0.5))
        signals.service_criticality = self.service_criticality_map.get(service, 0.5)

        # --- log_level_score: mapeamento para score normalizado ---
        level = str(metadata.get("level", "")).upper()
        signals.log_level_score = _LOG_LEVEL_SCORES.get(level, 0.2)

        # --- vector_score: score FAISS já normalizado (produto interno, 0–1) ---
        raw_vector = float(getattr(result, "score", 0.0))
        signals.vector_score = max(0.0, min(raw_vector, 1.0))

        # --- chunk_weight: campo opcional no metadata ---
        raw_weight = float(metadata.get("weight", 0.0))
        signals.chunk_weight = max(0.0, min(raw_weight, 1.0))

        # temporal_recency é preenchido em rerank() pois depende do batch inteiro

        return signals

    def _fuse_scores(self, signals: RankSignals) -> float:
        """Calcula score final por fusão ponderada dos sinais de domínio.

        Args:
            signals: RankSignals com todos os campos preenchidos.

        Returns:
            Float no intervalo [0.0, 1.0] representando o score de relevância.
        """
        score = (
            _WEIGHTS["deviation_sigma"] * signals.deviation_sigma
            + _WEIGHTS["service_criticality"] * signals.service_criticality
            + _WEIGHTS["log_level"] * signals.log_level_score
            + _WEIGHTS["vector_score"] * signals.vector_score
            + _WEIGHTS["temporal_recency"] * signals.temporal_recency
            + _WEIGHTS["chunk_weight"] * signals.chunk_weight
        )
        return max(0.0, min(score, 1.0))

    def _compute_temporal_recency(self, results: list[object]) -> list[float]:
        """Calcula recência temporal relativa ao batch.

        O resultado mais recente recebe 1.0; o mais antigo recebe 0.0.
        Resultados sem timestamp recebem 0.5 (valor neutro).

        Args:
            results: Lista de SearchResult do batch.

        Returns:
            Lista de scores de recência, um por resultado, na mesma ordem.
        """
        timestamps: list[float | None] = []
        for result in results:
            chunk = result.chunk  # type: ignore[attr-defined]
            metadata: dict = chunk.metadata if hasattr(chunk, "metadata") else {}
            ts_raw = metadata.get("timestamp")
            if ts_raw is None:
                timestamps.append(None)
            else:
                try:
                    import datetime
                    if isinstance(ts_raw, datetime.datetime):
                        timestamps.append(ts_raw.timestamp())
                    else:
                        timestamps.append(float(ts_raw))
                except (TypeError, ValueError):
                    timestamps.append(None)

        valid_ts = [t for t in timestamps if t is not None]
        if not valid_ts:
            return [0.5] * len(results)

        ts_min = min(valid_ts)
        ts_max = max(valid_ts)
        ts_range = ts_max - ts_min

        recency = []
        for ts in timestamps:
            if ts is None:
                recency.append(0.5)
            elif ts_range == 0.0:
                recency.append(1.0)
            else:
                recency.append((ts - ts_min) / ts_range)
        return recency

    def rerank(
        self,
        results: list[object],
        query: str,
        top_k: int = 10,
        use_tier2: bool = False,
    ) -> list[object]:
        """Reordena resultados de busca usando fusão de sinais de domínio.

        Tier 1: fusão ponderada de sinais de domínio (sempre ativo).
        Tier 2: refinamento cross-encoder opcional (ativado por use_tier2=True
                e disponibilidade do modelo).

        Args:
            results: Lista de SearchResult retornados pelo FAISS.
            query: Query original usada na busca.
            top_k: Número máximo de resultados a retornar.
            use_tier2: Se True, ativa refinamento cross-encoder nos top-50%.

        Returns:
            Lista de até top_k SearchResult ordenados por score de fusão decrescente.
        """
        if not results:
            return []

        # --- Tier 1: fusão de sinais de domínio ---
        recency_scores = self._compute_temporal_recency(results)

        tier1_results: list[tuple[object, float]] = []
        for result, recency in zip(results, recency_scores):
            signals = self._compute_signals(result)
            signals.temporal_recency = recency
            tier1_score = self._fuse_scores(signals)
            tier1_results.append((result, tier1_score))

        tier1_results.sort(key=lambda x: x[1], reverse=True)

        # --- Tier 2: cross-encoder opcional ---
        if use_tier2 and self.use_cross_encoder and self._cross_encoder is not None and len(results) > 10:
            tier1_results = self._apply_tier2(tier1_results, query)

        # Retorna apenas top_k, reescrevendo o score no objeto
        output = []
        for result, final_score in tier1_results[:top_k]:
            result.score = final_score  # type: ignore[attr-defined]
            output.append(result)
        return output

    def _apply_tier2(
        self,
        tier1_results: list[tuple[object, float]],
        query: str,
    ) -> list[tuple[object, float]]:
        """Aplica cross-encoder nos top-50% dos candidatos Tier 1.

        Fórmula: score_final = 0.6 × tier1 + 0.4 × cross_encoder_normalizado

        Args:
            tier1_results: Lista de (SearchResult, tier1_score) ordenada por score.
            query: Query original para o cross-encoder.

        Returns:
            Lista combinada de (SearchResult, final_score) ordenada por score final.
        """
        split = max(1, len(tier1_results) // 2)
        top_half = tier1_results[:split]
        bottom_half = tier1_results[split:]

        # Re-score com cross-encoder
        pairs = [(query, r.chunk.text) for r, _ in top_half]  # type: ignore[attr-defined]
        try:
            ce_scores_raw: list[float] = self._cross_encoder.predict(pairs).tolist()
        except Exception:
            try:
                ce_scores_raw = list(self._cross_encoder.predict(pairs))
            except Exception:
                # Fallback: retorna resultado Tier 1 inalterado
                return tier1_results

        # Normaliza cross-encoder scores para [0, 1]
        ce_min = min(ce_scores_raw)
        ce_max = max(ce_scores_raw)
        ce_range = ce_max - ce_min
        if ce_range == 0.0:
            ce_normalized = [1.0] * len(ce_scores_raw)
        else:
            ce_normalized = [(s - ce_min) / ce_range for s in ce_scores_raw]

        # Blending: 0.6 × Tier1 + 0.4 × CrossEncoder
        blended_top: list[tuple[object, float]] = []
        for (result, t1_score), ce_score in zip(top_half, ce_normalized):
            final = 0.6 * t1_score + 0.4 * ce_score
            blended_top.append((result, max(0.0, min(final, 1.0))))

        combined = blended_top + bottom_half
        combined.sort(key=lambda x: x[1], reverse=True)
        return combined
