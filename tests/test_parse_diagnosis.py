"""Testes unitários para _parse_diagnosis do DiagnosisAgent (FR5).

Valida parsing de saída LLM com tags <DIAGNOSIS>:
- JSON válido completo
- JSON com campos faltando
- Conteúdo não-JSON (fallback)
- String vazia (sem tags)
- JSON com múltiplos affected_services
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Casos parametrizados
# ---------------------------------------------------------------------------

_VALID_ALL_FIELDS = (
    "<DIAGNOSIS>"
    '{"root_cause": "Connection pool exhaustion in payment-svc",'
    ' "evidence": ["Pool exhausted x42"],'
    ' "affected_services": ["payment-svc"],'
    ' "confidence": "high",'
    ' "timeline": "Deploy at 10:00, errors at 10:05"}'
    "</DIAGNOSIS>",
    {
        "root_cause": "Connection pool exhaustion in payment-svc",
        "evidence": ["Pool exhausted x42"],
        "affected_services": ["payment-svc"],
        "confidence": "high",
        "timeline": "Deploy at 10:00, errors at 10:05",
    },
)

_MISSING_ROOT_CAUSE = (
    "<DIAGNOSIS>"
    '{"evidence": ["Some log error"],'
    ' "affected_services": ["auth-svc"],'
    ' "confidence": "low",'
    ' "timeline": "Unknown"}'
    "</DIAGNOSIS>",
    {
        "evidence": ["Some log error"],
        "affected_services": ["auth-svc"],
        "confidence": "low",
        "timeline": "Unknown",
    },
)

_NON_JSON_FALLBACK = (
    "<DIAGNOSIS>this is not valid JSON at all</DIAGNOSIS>",
    {"root_cause": "this is not valid JSON at all", "confidence": "medium", "evidence": []},
)

_EMPTY_STRING = (
    "",
    None,
)

_MULTIPLE_SERVICES = (
    "<DIAGNOSIS>"
    '{"root_cause": "Auth cascade failure",'
    ' "evidence": ["Timeout in auth-svc", "Payment cascade"],'
    ' "affected_services": ["auth-svc", "payment-svc", "gateway"],'
    ' "confidence": "medium",'
    ' "timeline": "auth slow at 10:00, cascaded at 10:01"}'
    "</DIAGNOSIS>",
    {
        "root_cause": "Auth cascade failure",
        "evidence": ["Timeout in auth-svc", "Payment cascade"],
        "affected_services": ["auth-svc", "payment-svc", "gateway"],
        "confidence": "medium",
        "timeline": "auth slow at 10:00, cascaded at 10:01",
    },
)

_PARSE_CASES = [
    pytest.param(*_VALID_ALL_FIELDS, id="valid_json_all_fields"),
    pytest.param(*_MISSING_ROOT_CAUSE, id="json_missing_root_cause"),
    pytest.param(*_NON_JSON_FALLBACK, id="non_json_fallback"),
    pytest.param(*_EMPTY_STRING, id="empty_string_returns_none"),
    pytest.param(*_MULTIPLE_SERVICES, id="multiple_affected_services"),
]


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("text,expected", _PARSE_CASES)
def test_parse_diagnosis_parametrized(text: str, expected: dict | None) -> None:
    """_parse_diagnosis retorna o resultado esperado para cada tipo de entrada."""
    from agents.diagnoser import DiagnosisAgent

    agent = DiagnosisAgent()
    result = agent._parse_diagnosis(text)
    assert result == expected


@pytest.mark.unit
def test_parse_diagnosis_returns_dict_on_valid_json() -> None:
    """_parse_diagnosis retorna um dict quando o JSON dentro das tags é válido."""
    from agents.diagnoser import DiagnosisAgent

    text = '<DIAGNOSIS>{"root_cause": "test", "confidence": "high", "evidence": []}</DIAGNOSIS>'
    result = DiagnosisAgent()._parse_diagnosis(text)
    assert isinstance(result, dict)
    assert result["root_cause"] == "test"


@pytest.mark.unit
def test_parse_diagnosis_no_closing_tag_returns_none() -> None:
    """_parse_diagnosis retorna None quando a tag </DIAGNOSIS> está ausente."""
    from agents.diagnoser import DiagnosisAgent

    text = "<DIAGNOSIS>some content without closing tag"
    result = DiagnosisAgent()._parse_diagnosis(text)
    assert result is None


@pytest.mark.unit
def test_parse_diagnosis_fallback_shape_matches_heuristic() -> None:
    """Fallback não-JSON produz dict com as chaves esperadas (compatível com _heuristic_diagnosis)."""
    from agents.diagnoser import DiagnosisAgent

    text = "<DIAGNOSIS>cannot parse this</DIAGNOSIS>"
    result = DiagnosisAgent()._parse_diagnosis(text)
    assert result is not None
    assert "root_cause" in result
    assert "confidence" in result
    assert "evidence" in result
