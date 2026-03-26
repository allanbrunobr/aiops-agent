"""Setup do índice FAISS para RAG — extraído de main.py (Story 4.1).

Encapsula criação e carregamento do ObservabilityIndexer de forma que
main.py possa delegar sem conter lógica de setup inline.
"""

from __future__ import annotations


def setup_faiss_index(config: object = None) -> object:
    """Cria e retorna um ObservabilityIndexer configurado.

    Lazy-imports sentence-transformers e faiss para evitar custo de startup.
    Retorna None se a importação falhar ou --no-rag estiver ativo.

    Args:
        config: Configuração da aplicação (opcional — usado para flags futuras).

    Returns:
        ObservabilityIndexer configurado, ou None se indisponível.
    """
    try:
        from rag.indexer import ObservabilityIndexer  # lazy import — sentence-transformers pesado
        return ObservabilityIndexer()
    except ImportError:
        return None
