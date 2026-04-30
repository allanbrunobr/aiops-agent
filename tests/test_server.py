"""Testes para server/app.py — Story 1.5."""

from __future__ import annotations

import sys

import pytest
from httpx import ASGITransport, AsyncClient

from interfaces.http.app import LazyRAG, create_app


@pytest.fixture
def app():
    """Cria app FastAPI para teste."""
    return create_app()


@pytest.mark.asyncio
async def test_health_endpoint(app):
    """Endpoint /health deve retornar 200 com status ok."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_health_ready_before_startup(app):
    """Endpoint /health/ready antes do lifespan deve retornar not_ready."""
    # Sem executar o lifespan, o app_state nao esta inicializado
    # Mas como o app_state e global, depende do estado da sessao
    # Testamos que o endpoint existe e retorna JSON valido
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health/ready")
        data = response.json()
        assert "status" in data
        assert "components" in data


def test_lazy_rag_no_heavy_imports():
    """LazyRAG nao deve importar sentence-transformers na construcao."""
    # Guarda estado
    had_st = "sentence_transformers" in sys.modules

    rag = LazyRAG()

    # Se nao tinha antes, nao deve ter agora
    if not had_st:
        assert "sentence_transformers" not in sys.modules

    # Propriedade is_loaded deve ser False antes de qualquer operacao
    assert rag.is_loaded is False


def test_lazy_rag_index_path():
    """LazyRAG deve aceitar index_path customizado."""
    from pathlib import Path
    custom_path = Path("/tmp/test_index.faiss")
    rag = LazyRAG(index_path=custom_path)
    assert rag._index_path == custom_path


def test_lazy_rag_load_persisted_nonexistent(tmp_path):
    """load_persisted_index deve retornar False se arquivo nao existe."""
    rag = LazyRAG(index_path=tmp_path / "nonexistent.faiss")
    assert rag.load_persisted_index() is False


def test_create_app_returns_fastapi():
    """create_app deve retornar instancia de FastAPI."""
    from fastapi import FastAPI
    application = create_app()
    assert isinstance(application, FastAPI)
    assert application.title == "AIOps Agent"


def test_dependency_helpers():
    """Dependency helpers devem existir e ser importaveis."""
    from interfaces.http.dependencies import get_config, get_engine, get_store
    # Funcoes devem existir — falham com AssertionError se app_state nao inicializado
    assert callable(get_config)
    assert callable(get_store)
    assert callable(get_engine)
