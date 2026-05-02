from __future__ import annotations

"""Testes unitários da camada de narrativa do dashboard de demonstração.

Cobre o módulo `interfaces/demo/narratives.py`: registro `NARRATIVES`,
helper `get_narrative` e contrato dos dataclasses frozen
(`SlackMockMessage`, `PhaseNarrative`, `StoryNarrative`).
"""

import dataclasses

import pytest

from interfaces.demo.narratives import (
    NARRATIVES,
    PhaseNarrative,
    SlackMockMessage,
    StoryNarrative,
    get_narrative,
)

_EXPECTED_PHASES = {"PLAYING", "DEGRADING", "WARNING", "ALERT", "INCIDENT", "RESOLVED"}
_EXPECTED_SCENARIOS = {"payment-pool-exhaustion", "payment-pool-recovery"}


@pytest.mark.unit
def test_narratives_dict_has_two_scenarios() -> None:
    """NARRATIVES expõe exatamente os dois cenários documentados."""
    assert len(NARRATIVES) == 2
    assert set(NARRATIVES.keys()) == _EXPECTED_SCENARIOS


@pytest.mark.unit
def test_each_narrative_has_six_phases() -> None:
    """Cada cenário cobre as 6 fases canônicas do pipeline."""
    for scenario_name, story in NARRATIVES.items():
        assert len(story.phases) == 6, f"{scenario_name}: phases={list(story.phases)}"
        assert set(story.phases.keys()) == _EXPECTED_PHASES, (
            f"{scenario_name}: faltando fases {_EXPECTED_PHASES - set(story.phases)}"
        )


@pytest.mark.unit
@pytest.mark.parametrize("scenario", sorted(_EXPECTED_SCENARIOS))
def test_get_narrative_returns_story_for_known_scenario(scenario: str) -> None:
    """get_narrative devolve StoryNarrative para cada cenário registrado."""
    story = get_narrative(scenario)
    assert isinstance(story, StoryNarrative)
    assert story.scenario_name == scenario


@pytest.mark.unit
def test_get_narrative_returns_none_for_unknown() -> None:
    """get_narrative devolve None silenciosamente para cenário inexistente."""
    assert get_narrative("nope") is None


@pytest.mark.unit
def test_dataclasses_are_frozen() -> None:
    """SlackMockMessage, PhaseNarrative e StoryNarrative recusam mutação."""
    slack = SlackMockMessage(
        channel="#x",
        author="bot",
        title="t",
        body="b",
        severity="info",
        timestamp_label="+0 min",
    )
    phase = PhaseNarrative(
        timeline_label="00:00",
        business_impact_per_min=0,
        impact_color="green",
        hero_text=None,
        hero_lead_time_seconds=None,
        slack_message=None,
    )
    story = StoryNarrative(
        scenario_name="x",
        app_name="App",
        app_context="ctx",
        baseline_revenue_per_min=1,
        phases={"PLAYING": phase},
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        slack.channel = "#other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        phase.timeline_label = "mut"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        story.scenario_name = "mut"  # type: ignore[misc]
