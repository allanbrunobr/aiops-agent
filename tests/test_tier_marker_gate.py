"""Portão de coleta: todo teste precisa de um marcador de nível (T-a).

Sem marcador, o teste só rodava no job rust-build — 39% da suíte, inclusive
testes de segurança, ficava fora dos jobs rápidos sem nenhum aviso.
"""
from __future__ import annotations

import pytest

from tests.conftest import TIER_MARKERS, untiered_items


class _Item:
    def __init__(self, nodeid, markers):
        self.nodeid = nodeid
        self._markers = set(markers)

    def get_closest_marker(self, name):
        return name if name in self._markers else None


@pytest.mark.unit
class TestTierMarkerGate:
    def test_items_without_tier_are_reported(self):
        items = [_Item("a::ok", ["unit"]), _Item("b::slow_only", ["slow"]), _Item("c::none", [])]
        assert untiered_items(items) == ["b::slow_only", "c::none"]

    @pytest.mark.parametrize("tier", TIER_MARKERS)
    def test_each_tier_satisfies_the_gate(self, tier):
        assert untiered_items([_Item("x::t", [tier])]) == []

    def test_whole_session_is_marked(self, request):
        """Na suíte inteira (job rust-build) todo item coletado tem nível."""
        assert untiered_items(request.session.items) == []
