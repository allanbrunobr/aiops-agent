"""O postmortem não se contradiz, em nenhuma combinação de saída.

Quarto pré-PR (00cb1c5) e revisão do step_postmortem inteiro (9db0afd):
- com aprovação pendente, o Summary dizia `Resolution: Resolved` e as Lições,
  no mesmo documento, que o pipeline parou no gate humano;
- numa saída antecipada (sem incidente, diagnóstico FAILED), as Lições
  diziam "completed all stages", e com root_cause None o postmortem quebrava;
- a Timeline e o título diziam "Mitigation applied", mas nada executa a ação;
- a transição gravada antes do gate dizia "human approval still pending";
- o JSON dizia meta.success=true sem o veredito do gate;
- a lição dizia "Heuristic diagnosis" mesmo com o LLM.
"""
from __future__ import annotations

import inspect
import json
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

ACTION = SimpleNamespace(description="rollback", command="kubectl rollout undo deployment/x",
                         risk_level="LOW", rollback_command=None)


def _postmortem(tmp_path, result, fmt="markdown"):
    import main
    from data.sample_incident import create_incident

    md, js = main.step_postmortem(result, create_incident(), output_dir=str(tmp_path),
                                  output_format=fmt)
    return (md.read_text() if md else None), (json.loads(js.read_text()) if js else None)


def _proposed(tnr="not_verified", requires_human=True, **extra):
    return {
        "success": True,
        "stages": {
            "detection": {"is_incident": True},
            "diagnosis": {"root_cause": "rc", "confidence": "high", "evidence": []},
            "mitigation": {"action": ACTION},
            "verification": {"result": tnr},
            "safety_decision": {"requires_human": requires_human,
                                "blocked_by": ["pre_validator"] if requires_human else []},
        },
        "timeline": [],
        **extra,
    }


def test_pending_approval_is_not_reported_as_resolved(tmp_path):
    text, _ = _postmortem(tmp_path, _proposed())
    assert "- **Resolution**: Resolved" not in text
    assert "- **Resolution**: Awaiting human approval" in text
    assert "Pipeline stopped at the human approval gate" in text
    assert "TNR verification validated" not in text


def test_auto_approved_success_is_resolved(tmp_path):
    text, _ = _postmortem(tmp_path, _proposed(tnr="success", requires_human=False))
    assert "- **Resolution**: Resolved\n" in text
    assert "TNR verification validated the mitigation outcome" in text
    assert "completed all stages (mitigation approved; execution is outside the agent)" in text


def test_auto_approved_without_measurement_says_not_executed(tmp_path):
    text, _ = _postmortem(tmp_path, _proposed(tnr="not_verified", requires_human=False))
    assert ("- **Resolution**: Resolved (mitigation approved, not executed by the agent; "
            "TNR not_verified)") in text
    assert "TNR did not verify the mitigation" in text


@pytest.mark.parametrize("result", [
    _proposed(),
    _proposed(tnr="success", requires_human=False),
    {"stages": {"detection": {"is_incident": False}}, "timeline": []},
], ids=["pending", "auto", "no-incident"])
def test_no_combination_claims_the_mitigation_was_applied(tmp_path, result):
    text, _ = _postmortem(tmp_path, result)
    assert "Mitigation applied" not in text
    assert "## Mitigation Applied" not in text
    assert "## Mitigation Proposed" in text


def test_early_exit_does_not_claim_completed_stages(tmp_path):
    """Sem incidente: o pipeline volta a IDLE antes da mitigação (main.py)."""
    text, _ = _postmortem(tmp_path, {
        "stages": {"detection": {"is_incident": False}},
        "timeline": [{"time": "10:00:01", "from": "DETECTING", "to": "IDLE",
                      "reason": "Nenhum incidente detectado"}],
    })
    assert "completed all stages" not in text
    assert "Pipeline stopped before mitigation" in text
    assert "identified the incident promptly" not in text
    assert "found no incident" in text
    assert "- **Impact**: No incident detected" in text
    assert "No mitigation was proposed" in text
    assert "- **Resolution**: Requires manual intervention" in text


def test_failed_diagnosis_with_no_root_cause_does_not_crash(tmp_path):
    text, _ = _postmortem(tmp_path, {
        "stages": {"detection": {"is_incident": True},
                   "diagnosis": {"root_cause": None, "confidence": None, "evidence": None}},
        "timeline": [{"time": "10:00:02", "from": "DIAGNOSING", "to": "FAILED",
                      "reason": "diagnóstico falhou"}],
    })
    assert "Unable to determine root cause" in text
    assert "Diagnosis failed" in text
    assert "completed all stages" not in text


def test_diagnosis_lesson_names_the_llm(tmp_path):
    text, _ = _postmortem(tmp_path, _proposed(llm_model="claude-sonnet-x"))
    assert "LLM diagnosis (claude-sonnet-x) provided high-confidence assessment" in text
    assert "Heuristic diagnosis" not in text
    heuristic, _ = _postmortem(tmp_path, _proposed(llm_model="heuristic"))
    assert "Heuristic diagnosis provided high-confidence assessment" in heuristic


def test_json_carries_the_same_verdict_as_markdown(tmp_path):
    text, data = _postmortem(tmp_path, _proposed(), fmt="both")
    meta = data["meta"]
    assert meta["awaiting_human"] is True
    assert meta["resolution"] in text
    assert meta["safety_blocked_by"] == ["pre_validator"]
    assert meta["mitigation_proposed"] is True
    # As 10 chaves de topo são API estável (CONTRIBUTING.md).
    assert len(data) == 10


def test_transition_before_the_gate_does_not_claim_pending_approval():
    """step_agents grava a transição VERIFYING -> RESOLVED antes de o gate
    (step_safety_validation) decidir; ela não pode afirmar o veredito."""
    import main

    assert "human approval still pending" not in inspect.getsource(main.step_agents)
