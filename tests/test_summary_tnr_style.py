"""O resumo da CLI não pinta `not_verified` de vermelho.

P2-11 trocou o sucesso inventado do TNR por `not_verified`, e o reporter
(adapters/reporter/rich_console.py) o mostra em amarelo. O `print_summary` do
main.py usava "verde se success, senão vermelho" e mostrava como falha uma
verificação que só não foi medida (achado secundário do pré-PR).
"""
from __future__ import annotations

import pytest
from rich.console import Console

pytestmark = pytest.mark.unit


def _style_of(monkeypatch, tnr_result: str) -> str | None:
    import main

    console = Console(record=True, force_terminal=True, color_system="truecolor", width=200)
    monkeypatch.setattr(main, "console", console)
    main.print_summary({"success": True, "stages": {
        "verification": {"result": tnr_result, "message": "m"}}})
    for seg in console._record_buffer:
        if seg.text == tnr_result and seg.style and seg.style.color:
            return seg.style.color.name
    return None


@pytest.mark.parametrize(("tnr_result", "color"), [
    ("success", "green"), ("not_verified", "yellow"), ("regression", "red"),
])
def test_tnr_row_color(monkeypatch, tnr_result, color):
    assert _style_of(monkeypatch, tnr_result) == color
