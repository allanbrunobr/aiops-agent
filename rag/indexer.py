"""
Indexador RAG com FAISS + Sentence-Transformers.

Indexa dados de observabilidade pré-processados para busca semântica.
Suporta persistência em disco para evitar re-embedding entre execuções.
Baseado nos padrões de RAG-Based-DevOps-Log-Analysis (github.com/priyanshmathur).
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import sys

import numpy as np
from dataclasses import dataclass


@dataclass
class IndexedChunk:
    """Um chunk indexado no FAISS."""
    text: str
    source: str  # "log", "metric", "trace"
    metadata: dict


class ObservabilityIndexer:
    """Indexa dados de observabilidade para busca semântica."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self.model = None
        self.index = None
        self.chunks: list[IndexedChunk] = []

    def _load_model(self):
        """Lazy load do modelo de embeddings."""
        if self.model is None:
            from sentence_transformers import SentenceTransformer
            self.model = SentenceTransformer(self.model_name)

    def build_index(self, log_templates: list, metric_anomalies: list, slow_traces: list):
        """Constrói o índice FAISS a partir dos dados processados."""
        import faiss

        self._load_model()
        self.chunks = []

        # Indexa log templates
        for tpl in log_templates:
            svcs = ", ".join(sorted(tpl.services))
            text = (
                f"[{tpl.level}] Service: {svcs}. "
                f"Log pattern: {tpl.template}. "
                f"Occurrences: {tpl.count}. "
                f"Time range: {tpl.first_seen} to {tpl.last_seen}."
            )
            self.chunks.append(IndexedChunk(
                text=text, source="log",
                metadata={"level": tpl.level, "count": tpl.count, "services": list(tpl.services)},
            ))

        # Indexa anomalias de métricas
        for anomaly in metric_anomalies:
            text = (
                f"Metric anomaly in {anomaly.service}/{anomaly.metric_name}. "
                f"Type: {anomaly.anomaly_type}. "
                f"Value: {anomaly.value} (baseline: {anomaly.mean}, deviation: {anomaly.deviation_sigma} sigma). "
                f"Detected at: {anomaly.timestamp}."
            )
            self.chunks.append(IndexedChunk(
                text=text, source="metric",
                metadata={"service": anomaly.service, "metric": anomaly.metric_name,
                           "deviation": anomaly.deviation_sigma},
            ))

        # Indexa traces lentos
        for trace in slow_traces:
            path_str = " -> ".join(trace.path)
            attrs = ", ".join(f"{k}={v}" for k, v in trace.attributes.items()) if trace.attributes else "none"
            text = (
                f"Slow trace: {path_str}. "
                f"Duration: {trace.total_duration_ms:.0f}ms (normal: {trace.normal_duration_ms:.0f}ms). "
                f"Bottleneck: {trace.bottleneck_service} ({trace.bottleneck_duration_ms:.0f}ms). "
                f"Attributes: {attrs}."
            )
            self.chunks.append(IndexedChunk(
                text=text, source="trace",
                metadata={"path": trace.path, "duration_ms": trace.total_duration_ms,
                           "bottleneck": trace.bottleneck_service},
            ))

        # Gera embeddings e constrói índice
        texts = [chunk.text for chunk in self.chunks]
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        embeddings = np.array(embeddings, dtype=np.float32)

        self.index = faiss.IndexFlatIP(embeddings.shape[1])
        self.index.add(embeddings)

        return len(self.chunks)

    def save_index(self, cache_dir: str, content_hash: str) -> None:
        """Salva índice FAISS + chunks + metadados em disco.

        Cria 3 arquivos no cache_dir:
        - faiss.index: binário nativo do FAISS
        - chunks.json: lista de chunks serializados
        - index_meta.json: hash de conteúdo + metadados

        Args:
            cache_dir: Diretório para salvar os arquivos de cache.
            content_hash: SHA-256 do conteúdo indexado para detecção de stale.

        Raises:
            ValueError: Se o índice não foi construído (self.index is None).
        """
        if self.index is None:
            raise ValueError("Índice FAISS não construído — chame build_index() antes de save_index()")

        import faiss

        os.makedirs(cache_dir, exist_ok=True)

        # 1. Salva índice FAISS (binário nativo)
        faiss.write_index(self.index, os.path.join(cache_dir, "faiss.index"))

        # 2. Salva chunks como JSON (IndexedChunk é serializável)
        chunks_data = [
            {"text": c.text, "source": c.source, "metadata": c.metadata}
            for c in self.chunks
        ]
        with open(os.path.join(cache_dir, "chunks.json"), "w", encoding="utf-8") as f:
            json.dump(chunks_data, f, ensure_ascii=False, indent=2)

        # 3. Salva metadados do índice
        meta = {
            "content_hash": content_hash,
            "model_name": self.model_name,
            "num_chunks": len(self.chunks),
            "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        with open(os.path.join(cache_dir, "index_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

    def load_index(self, cache_dir: str, content_hash: str) -> bool:
        """Carrega índice FAISS + chunks de cache em disco.

        Valida content_hash contra o hash armazenado para detectar índice stale.
        Em caso de falha (arquivo corrompido, hash diferente, arquivo faltando),
        retorna False para que o caller reconstrua o índice.

        NÃO chama _load_model() — o caller deve chamar separadamente.

        Args:
            cache_dir: Diretório do cache.
            content_hash: SHA-256 do conteúdo atual para validação de stale.

        Returns:
            True se carregou com sucesso, False se precisa reconstruir.
        """
        try:
            # Verifica existência do diretório e arquivos obrigatórios
            meta_path = os.path.join(cache_dir, "index_meta.json")
            chunks_path = os.path.join(cache_dir, "chunks.json")
            faiss_path = os.path.join(cache_dir, "faiss.index")

            if not os.path.isdir(cache_dir):
                return False

            if not all(os.path.isfile(p) for p in [meta_path, chunks_path, faiss_path]):
                print(f"[RAG Cache] Arquivos de cache incompletos em {cache_dir}", file=sys.stderr)
                return False

            # Carrega e valida metadados
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)

            if meta.get("content_hash") != content_hash:
                print(
                    f"[RAG Cache] Hash de conteúdo diferente — índice stale "
                    f"(cache={meta.get('content_hash', '?')[:12]}... vs atual={content_hash[:12]}...)",
                    file=sys.stderr,
                )
                return False

            # Valida model_name para evitar mismatch de embeddings
            cached_model = meta.get("model_name")
            if cached_model != self.model_name:
                print(
                    f"[RAG Cache] Modelo diferente — embeddings incompatíveis "
                    f"(cache={cached_model} vs atual={self.model_name})",
                    file=sys.stderr,
                )
                return False

            # Carrega chunks do JSON
            with open(chunks_path, "r", encoding="utf-8") as f:
                chunks_data = json.load(f)

            self.chunks = [
                IndexedChunk(text=c["text"], source=c["source"], metadata=c["metadata"])
                for c in chunks_data
            ]

            # Carrega índice FAISS (binário) — import lazy para evitar carga em testes
            import faiss
            self.index = faiss.read_index(faiss_path)

            return True

        except Exception as exc:
            print(f"[RAG Cache] Falha ao carregar cache: {exc}", file=sys.stderr)
            # Reseta estado para evitar dados parciais
            self.chunks = []
            self.index = None
            return False

    @staticmethod
    def _compute_content_hash(log_templates: list, metric_anomalies: list, slow_traces: list) -> str:
        """Calcula SHA-256 do conteúdo a ser indexado.

        Gera representação textual determinística dos dados de entrada,
        ordenada para garantir mesmo hash independente da ordem de entrada.
        Usa a mesma lógica de construção de texto de build_index() para
        capturar exatamente o que seria indexado.

        Args:
            log_templates: Templates de log do aggregator.
            metric_anomalies: Anomalias de métricas do metric_filter.
            slow_traces: Traces lentos do trace_filter.

        Returns:
            Hex digest SHA-256 de 64 caracteres.
        """
        parts = []

        # Logs — ordena por template text para determinismo
        for tpl in sorted(log_templates, key=lambda t: str(t.template)):
            svcs = ", ".join(sorted(tpl.services))
            parts.append(
                f"LOG|{tpl.level}|{svcs}|{tpl.template}|{tpl.count}|{tpl.first_seen}|{tpl.last_seen}"
            )

        # Métricas — ordena por service+metric_name
        for a in sorted(metric_anomalies, key=lambda m: f"{m.service}/{m.metric_name}"):
            parts.append(
                f"METRIC|{a.service}|{a.metric_name}|{a.anomaly_type}|{a.value}|{a.mean}|{a.deviation_sigma}|{a.timestamp}"
            )

        # Traces — ordena por path joined
        for t in sorted(slow_traces, key=lambda tr: "->".join(tr.path)):
            attrs_str = ",".join(f"{k}={v}" for k, v in sorted(t.attributes.items())) if t.attributes else ""
            parts.append(
                f"TRACE|{'->'.join(t.path)}|{t.total_duration_ms}|{t.normal_duration_ms}|"
                f"{t.bottleneck_service}|{t.bottleneck_duration_ms}|{attrs_str}"
            )

        content = "\n".join(parts)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def get_all_chunks(self) -> list[IndexedChunk]:
        """Retorna todos os chunks indexados."""
        return self.chunks


class ObservabilityIndexerWithHistory(ObservabilityIndexer):
    """Indexador FAISS com suporte a chunks históricos de incidentes anteriores.

    Extende ObservabilityIndexer adicionando chunks do banco de memória
    com peso reduzido para enriquecer o índice sem dominar os resultados.
    """

    def __init__(
        self,
        model_name: str = "all-MiniLM-L6-v2",
        db_path: str = "data/incident_memory.db",
    ) -> None:
        super().__init__(model_name=model_name)
        self.db_path = db_path

    def build_index_with_history(
        self,
        log_templates: list,
        metric_anomalies: list,
        slow_traces: list,
        include_history: bool = True,
        max_historical_chunks: int = 3,
    ) -> int:
        """Constrói índice FAISS com chunks atuais e opcionalmente históricos.

        Chunks atuais recebem chunk_weight=1.0 nos metadados.
        Chunks históricos recebem chunk_weight=0.5 e is_historical=True.

        Args:
            log_templates: Templates de log do aggregator.
            metric_anomalies: Anomalias de métricas.
            slow_traces: Traces lentos.
            include_history: Se True, adiciona chunks históricos ao índice.
            max_historical_chunks: Máximo de chunks históricos a incluir.

        Returns:
            Número total de chunks indexados.
        """
        import faiss

        self._load_model()
        self.chunks = []

        # Indexa chunks atuais (lógica idêntica ao build_index base)
        for tpl in log_templates:
            svcs = ", ".join(sorted(tpl.services))
            text = (
                f"[{tpl.level}] Service: {svcs}. "
                f"Log pattern: {tpl.template}. "
                f"Occurrences: {tpl.count}. "
                f"Time range: {tpl.first_seen} to {tpl.last_seen}."
            )
            self.chunks.append(IndexedChunk(
                text=text,
                source="log",
                metadata={"level": tpl.level, "count": tpl.count, "services": list(tpl.services), "chunk_weight": 1.0},
            ))

        for anomaly in metric_anomalies:
            text = (
                f"Metric anomaly in {anomaly.service}/{anomaly.metric_name}. "
                f"Type: {anomaly.anomaly_type}. "
                f"Value: {anomaly.value} (baseline: {anomaly.mean}, deviation: {anomaly.deviation_sigma} sigma). "
                f"Detected at: {anomaly.timestamp}."
            )
            self.chunks.append(IndexedChunk(
                text=text,
                source="metric",
                metadata={
                    "service": anomaly.service,
                    "metric": anomaly.metric_name,
                    "deviation": anomaly.deviation_sigma,
                    "chunk_weight": 1.0,
                },
            ))

        for trace in slow_traces:
            path_str = " -> ".join(trace.path)
            attrs = ", ".join(f"{k}={v}" for k, v in trace.attributes.items()) if trace.attributes else "none"
            text = (
                f"Slow trace: {path_str}. "
                f"Duration: {trace.total_duration_ms:.0f}ms (normal: {trace.normal_duration_ms:.0f}ms). "
                f"Bottleneck: {trace.bottleneck_service} ({trace.bottleneck_duration_ms:.0f}ms). "
                f"Attributes: {attrs}."
            )
            self.chunks.append(IndexedChunk(
                text=text,
                source="trace",
                metadata={
                    "path": trace.path,
                    "duration_ms": trace.total_duration_ms,
                    "bottleneck": trace.bottleneck_service,
                    "chunk_weight": 1.0,
                },
            ))

        # Adiciona chunks históricos (degradação graciosa em caso de erro)
        if include_history:
            try:
                from rag.memory import IncidentMemoryDB

                db = IncidentMemoryDB(db_path=self.db_path)
                with db._connect() as conn:
                    rows = conn.execute(
                        "SELECT * FROM incidents ORDER BY timestamp DESC LIMIT ?",
                        (max_historical_chunks,),
                    ).fetchall()

                import json as _json

                for row in rows:
                    services = _json.loads(row["affected_services"])
                    tags = _json.loads(row["tags"])
                    text = (
                        f"[HISTORICAL] Past incident: {row['scenario']}. "
                        f"Root cause: {row['root_cause']}. "
                        f"Affected services: {', '.join(services)}. "
                        f"Tags: {', '.join(tags)}. "
                        f"Confidence: {row['diagnosis_confidence']:.2f}. "
                        f"Resolution time: {row['resolution_time_sec']:.0f}s."
                    )
                    self.chunks.append(IndexedChunk(
                        text=text,
                        source="historical",
                        metadata={
                            "incident_id": row["incident_id"],
                            "scenario": row["scenario"],
                            "is_historical": True,
                            "chunk_weight": 0.5,
                        },
                    ))
            except Exception:
                # Degradação graciosa — continua sem histórico
                pass

        # Gera embeddings e constrói índice
        texts = [chunk.text for chunk in self.chunks]
        embeddings = self.model.encode(texts, normalize_embeddings=True)
        embeddings = np.array(embeddings, dtype=np.float32)

        self.index = faiss.IndexFlatIP(embeddings.shape[1])
        self.index.add(embeddings)

        return len(self.chunks)
