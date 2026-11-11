"""O postmortem não se contradiz entre o Summary e as Lições.

Quarto pré-PR (00cb1c5):
- com aprovação humana pendente, o Summary dizia `Resolution: Resolved` e as
  Lições, no mesmo documento, que o pipeline parou no gate humano;
- numa saída antecipada (sem incidente, diagnóstico FAILED), as Lições
  diziam "completed all stages" e "identified the incident promptly".
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _postmortem(tmp_path, result) -> str:
    import main
    from data.sample_incident import create_incident

    path, _ = main.step_postmortem(result, create_incident(), output_dir=str(tmp_path))
    return path.read_text()


def test_pending_approval_is_not_reported_as_resolved(tmp_path):
    text = _postmortem(tmp_path, {
        "success": True,
        "stages": {
            "detection": {"is_incident": True},
            "mitigation": {"action": None},
            "verification": {"result": "not_verified"},
            "safety_decision": {"requires_human": True},
        },
        "timeline": [],
    })
    assert "- **Resolution**: Resolved" not in text
    assert "- **Resolution**: Awaiting human approval" in text
    assert "Pipeline stopped at the human approval gate" in text
    assert "TNR verification validated" not in text


def test_auto_approved_success_is_resolved(tmp_path):
    text = _postmortem(tmp_path, {
        "success": True,
        "stages": {
            "detection": {"is_incident": True},
            "mitigation": {"action": None},
            "verification": {"result": "success"},
            "safety_decision": {"requires_human": False},
        },
        "timeline": [],
    })
    assert "- **Resolution**: Resolved" in text
    assert "TNR verification validated the mitigation outcome" in text
    assert "Incident response pipeline completed all stages" in text


def test_early_exit_does_not_claim_completed_stages(tmp_path):
    """Sem incidente: o pipeline volta a IDLE antes da mitigação (main.py)."""
    text = _postmortem(tmp_path, {
        "stages": {"detection": {"is_incident": False}},
        "timeline": [{"time": "10:00:01", "from": "DETECTING", "to": "IDLE",
                      "reason": "Nenhum incidente detectado"}],
    })
    assert "completed all stages" not in text
    assert "Pipeline stopped before mitigation" in text
    assert "identified the incident promptly" not in text
    assert "found no incident" in text
    assert "- **Resolution**: Requires manual intervention" in text
