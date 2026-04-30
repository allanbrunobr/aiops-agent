"""Testes para Story 2.2: Alert Normalizer Plugin Registry."""
from __future__ import annotations

import pytest

from core.protocols import CredentialProvider
from domain.alert import NormalizedAlert
from interfaces.http.webhook.normalizers.base import AlertNormalizer
from interfaces.http.webhook.normalizers.registry import NormalizerRegistry, register_normalizer


@pytest.fixture(autouse=True)
def clean_registry():
    """Limpa registry antes e depois de cada teste."""
    NormalizerRegistry.clear()
    yield
    NormalizerRegistry.clear()


def _make_minimal_normalizer(source: str) -> type[AlertNormalizer]:
    """Cria classe de normalizer minima para testes."""
    class _Normalizer(AlertNormalizer):
        @property
        def source_name(self) -> str:
            return source

        def validate_auth(self, headers, body, credentials):
            return True

        def normalize(self, payload):
            raise NotImplementedError

    return _Normalizer


def test_register_normalizer_decorator():
    """AC1: decorator registra normalizer com sucesso."""
    @register_normalizer("test_source")
    class TestNormalizer(AlertNormalizer):
        @property
        def source_name(self) -> str:
            return "test_source"

        def validate_auth(self, headers, body, credentials):
            return True

        def normalize(self, payload):
            raise NotImplementedError

    assert "test_source" in NormalizerRegistry.list_sources()
    normalizer = NormalizerRegistry.get("test_source")
    assert normalizer is not None
    assert isinstance(normalizer, AlertNormalizer)


def test_registry_get_returns_correct_normalizer():
    """AC2: registry.get() retorna normalizer correto para fonte registrada."""
    Cls = _make_minimal_normalizer("my_source")
    NormalizerRegistry.register("my_source", Cls())

    result = NormalizerRegistry.get("my_source")
    assert result is not None
    assert isinstance(result, AlertNormalizer)


def test_registry_get_returns_none_for_unknown_source():
    """AC2: registry.get() retorna None para fonte desconhecida."""
    assert NormalizerRegistry.get("nonexistent") is None


def test_duplicate_registration_raises():
    """AC4: registro duplicado levanta ValueError."""
    Cls = _make_minimal_normalizer("dup")
    NormalizerRegistry.register("dup", Cls())

    with pytest.raises(ValueError, match="Duplicate normalizer"):
        NormalizerRegistry.register("dup", Cls())


def test_list_sources_returns_all_registered():
    """AC: list_sources() retorna todas as fontes registradas."""
    Cls1 = _make_minimal_normalizer("src1")
    Cls2 = _make_minimal_normalizer("src2")
    NormalizerRegistry.register("src1", Cls1())
    NormalizerRegistry.register("src2", Cls2())

    sources = NormalizerRegistry.list_sources()
    assert "src1" in sources
    assert "src2" in sources


def test_normalizer_has_required_methods():
    """AC3: normalizer implementa normalize() e validate_auth()."""
    Cls = _make_minimal_normalizer("test")
    instance = Cls()
    assert hasattr(instance, "normalize")
    assert hasattr(instance, "validate_auth")
    assert hasattr(instance, "source_name")


def test_decorator_preserves_class_identity():
    """AC4: decorator retorna a mesma classe sem modificacao."""
    class Original(AlertNormalizer):
        @property
        def source_name(self) -> str:
            return "orig"

        def validate_auth(self, headers, body, credentials):
            return True

        def normalize(self, payload):
            raise NotImplementedError

    decorated = register_normalizer("orig")(Original)
    assert decorated is Original


def test_duplicate_registration_via_decorator_raises():
    """AC4: segundo decorator com mesma fonte levanta ValueError."""
    @register_normalizer("dup2")
    class First(AlertNormalizer):
        @property
        def source_name(self): return "dup2"
        def validate_auth(self, h, b, c): return True
        def normalize(self, p): raise NotImplementedError

    with pytest.raises(ValueError, match="Duplicate normalizer"):
        @register_normalizer("dup2")
        class Second(AlertNormalizer):
            @property
            def source_name(self): return "dup2"
            def validate_auth(self, h, b, c): return True
            def normalize(self, p): raise NotImplementedError
