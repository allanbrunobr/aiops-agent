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
    # A CLI sai com exit 0 ("resolved") sem incidente: o veredito não pode
    # pedir intervenção manual (pré-PR em 78c4689).
    assert "- **Resolution**: No incident detected; no action required" in text
    assert "Requires manual intervention" not in text


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
    assert "- **Resolution**: Requires manual intervention" in text


def test_tracking_survives_a_failed_diagnosis():
    """--tracker jira roda antes do postmortem; root_cause None dava len(None)."""
    import main

    class _Tracker:
        url = "https://jira.invalid"

        def __init__(self):
            self.calls = []

        def create_incident(self, title, severity, description):
            self.calls.append((title, severity, description))
            return "OPS-1"

    tracker = _Tracker()
    key = main.step_tracking({"stages": {"detection": {"severity": None},
                                         "diagnosis": {"root_cause": None, "evidence": None}}},
                             tracker)
    assert key == "OPS-1"
    assert tracker.calls[0][0] == "AIOps detected incident"
    assert tracker.calls[0][1] == "medium"


def test_replica_pipeline_uses_the_production_transition_text():
    """Os snapshots vêm da réplica do pipeline em conftest.py: ela tem de gravar
    o mesmo texto que main.step_agents, senão o snapshot não mede a correção."""
    import pathlib

    conftest = (pathlib.Path(__file__).parent / "conftest.py").read_text(encoding="utf-8")
    assert "aguardando aprovação" not in conftest
    assert "safety gate decides next" in conftest


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


# ── Pré-PR em 2b4f094: o resto do fluxo também sabe que não houve incidente ──

NO_INCIDENT = {"success": False, "stages": {"detection": {"is_incident": False, "severity": "low"}},
               "timeline": []}
FAILED_DIAGNOSIS = {
    "success": False,
    "stages": {"detection": {"is_incident": True, "severity": "high"},
               "diagnosis": {"root_cause": None, "is_incident": False}},
    "timeline": [{"time": "10:00", "from": "DIAGNOSING", "to": "FAILED", "reason": "x"}],
}


class _JiraLike:
    """Formata como o JiraTracker de verdade, sem rede."""

    url = "https://jira.invalid"

    def __init__(self):
        self.created = []

    def create_incident(self, title, severity, description):
        self.created.append(title)
        return "OPS-1"

    def create_postmortem(self, incident_id, timeline, root_cause, action_items):
        from tools.jira_tracker import JiraIncidentTracker

        self.body = JiraIncidentTracker._format_postmortem(timeline, root_cause, action_items)
        return "c-1"


def test_healthy_window_opens_no_tracker_issue():
    import main

    tracker = _JiraLike()
    assert main.step_tracking(NO_INCIDENT, tracker) is None
    assert tracker.created == []


def test_healthy_window_has_no_action_items(tmp_path):
    text, data = _postmortem(tmp_path, NO_INCIDENT, fmt="both")
    assert "No incident detected; no action required" in text
    assert "[ ]" not in text
    assert "- None (no incident detected)" in text
    assert data["action_items"] == []


def test_failed_diagnosis_postmortem_attaches_to_tracker():
    import main

    tracker = _JiraLike()
    assert main._attach_postmortem_to_tracker(tracker, "OPS-1", FAILED_DIAGNOSIS) == "c-1"
    assert "Unable to determine root cause" in tracker.body


@pytest.mark.parametrize(("result", "status"), [
    (NO_INCIDENT, "NO INCIDENT"),
    (FAILED_DIAGNOSIS, "UNRESOLVED"),
], ids=["no-incident", "failed-diagnosis"])
def test_cli_summary_matches_the_verdict(monkeypatch, result, status):
    """print_summary: janela saudável não é UNRESOLVED (exit 0), e
    root_cause None não derruba o resumo (que levava o run a exit 1)."""
    from rich.console import Console

    import main

    console = Console(record=True, width=200)
    monkeypatch.setattr(main, "console", console)
    main.print_summary(result)
    out = console.export_text()
    assert status in out
    if status == "NO INCIDENT":
        assert "UNRESOLVED" not in out


# ── Pré-PR em 5e418c3: tracker e Slack também reconhecem a janela saudável ──

def _summary_text(monkeypatch, result, tracker_info):
    from rich.console import Console

    import main

    console = Console(record=True, width=200)
    monkeypatch.setattr(main, "console", console)
    main.print_summary(result, tracker_info=tracker_info)
    return console.export_text()


def test_skipped_tracker_on_healthy_window_is_not_a_tracker_error(monkeypatch):
    import main

    tracker = _JiraLike()
    key = main.step_tracking(NO_INCIDENT, tracker)
    out = _summary_text(monkeypatch, NO_INCIDENT, {"issue_key": key, "url": None})
    assert "no incident detected" in out
    assert "tracker error" not in out


def test_real_tracker_failure_is_still_reported(monkeypatch):
    out = _summary_text(monkeypatch, FAILED_DIAGNOSIS, {"issue_key": None, "url": None})
    assert "tracker error" in out


def test_slack_final_message_for_a_healthy_window():
    from datetime import datetime, timezone

    from adapters.credentials import EnvCredentialProvider
    from core.lifecycle import LifecycleEvent
    from interfaces.http.notifications.slack import SlackNotifier

    notifier = SlackNotifier(channel="C1", creds=EnvCredentialProvider(), dry_run=True)
    event = LifecycleEvent(event_type="pipeline.completed", node_name=None,
                           correlation_id="inc-x", timestamp=datetime.now(timezone.utc),
                           data={"no_incident": True, "root_cause": "", "total_duration_ms": 5})
    text = json.dumps(notifier._build_final_blocks(event))
    assert "No Incident Detected" in text
    assert "Diagnosis Complete" not in text
    assert "Proposed Fix" not in text


def test_cli_does_not_announce_a_diagnosis_without_incident():
    """O disparo de diagnosis.completed em main() fica dentro de `if not
    _no_incident(result)`, e o pipeline.completed leva a marca no_incident."""
    import ast

    import main

    tree = ast.parse(inspect.getsource(main.main))
    guarded, flagged = False, False
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "_no_incident" in ast.unparse(node.test):
            guarded |= any(isinstance(c, ast.Constant) and c.value == "diagnosis.completed"
                           for c in ast.walk(node))
        if isinstance(node, ast.Call) and any(
                isinstance(a, ast.Constant) and a.value == "pipeline.completed" for a in node.args):
            flagged |= "no_incident" in ast.unparse(node)
    assert guarded and flagged


@pytest.mark.parametrize("with_initial_message", [True, False], ids=["chat_update", "chat_postMessage"])
@pytest.mark.parametrize(("no_incident", "expected"), [
    (True, "No incident detected: inc-x"),
    (False, "Incident inc-x: Diagnosis complete"),
], ids=["healthy", "incident"])
def test_slack_final_notification_text_matches_the_blocks(with_initial_message, no_incident, expected):
    """O `text` enviado à API (notificação, prévia, leitor de tela) dizia
    "Diagnosis complete" mesmo com o header "No Incident Detected"."""
    import asyncio
    from datetime import datetime, timezone
    from unittest.mock import AsyncMock

    from adapters.credentials import EnvCredentialProvider
    from core.lifecycle import LifecycleEvent
    from interfaces.http.notifications.slack import SlackNotifier

    notifier = SlackNotifier(channel="C1", creds=EnvCredentialProvider(), dry_run=True)
    notifier._dry_run = False
    notifier._client = AsyncMock()
    if with_initial_message:
        notifier._message_ts["inc-x"] = "1.0"
    event = LifecycleEvent(event_type="pipeline.completed", node_name=None, correlation_id="inc-x",
                           timestamp=datetime.now(timezone.utc),
                           data={"no_incident": no_incident, "total_duration_ms": 5})
    asyncio.run(notifier._post_final_message(event))
    call = (notifier._client.chat_update if with_initial_message
            else notifier._client.chat_postMessage).call_args
    assert call.kwargs["text"] == expected
    header = call.kwargs["blocks"][0]["text"]["text"]
    assert ("No Incident Detected" in header) is no_incident
