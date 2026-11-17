"""F5-2: fonte única do mapa de confiança (domain/confidence.py).

Antes havia três cópias de ``{"high": 0.85, "medium": 0.5, "low": 0.25}``
(CLI V0 ×2, MCP tools) com semânticas ligeiramente diferentes para valores
numéricos e desconhecidos.
"""

from __future__ import annotations

import pytest

from domain.confidence import CONFIDENCE_SCORES, confidence_to_score


@pytest.mark.unit
@pytest.mark.parametrize(
    "label, expected",
    [("high", 0.85), ("medium", 0.5), ("low", 0.25), ("HIGH", 0.85), (" Low ", 0.25)],
)
def test_labels_map_case_insensitively(label, expected):
    assert confidence_to_score(label) == expected


@pytest.mark.unit
def test_numeric_is_preserved():
    assert confidence_to_score(0.9) == 0.9
    assert confidence_to_score(1) == 1.0


@pytest.mark.unit
def test_unknown_and_none_use_default():
    assert confidence_to_score("maybe") == 0.0
    assert confidence_to_score(None) == 0.0
    assert confidence_to_score("maybe", default=0.5) == 0.5
    assert confidence_to_score(None, default=0.5) == 0.5


@pytest.mark.unit
def test_bool_is_not_treated_as_number():
    assert confidence_to_score(True) == 0.0


@pytest.mark.unit
def test_mcp_alias_points_to_the_same_mapping():
    """interfaces.mcp.tools expõe _CONFIDENCE_SCORES como alias da fonte única."""
    from interfaces.mcp.tools import _CONFIDENCE_SCORES

    assert _CONFIDENCE_SCORES is CONFIDENCE_SCORES
