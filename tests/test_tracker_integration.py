"""Testes de integracao para tracker CLI e pipeline.

Valida o fluxo completo: --tracker flag, instanciacao via registry,
criacao de incidente, anexacao de postmortem, e tratamento de erros.
Todos os testes mockam o JiraIncidentTracker sem fazer chamadas reais.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from main import (
    TRACKER_REGISTRY,
    _attach_postmortem_to_tracker,
    _resolve_tracker,
    build_arg_parser,
    step_postmortem,
    step_tracking,
    print_summary,
)


# --- Fixtures ---

@pytest.fixture
def sample_result():
    """Resultado minimo de pipeline para testes de tracker."""
    return {
        "success": True,
        "scenario_name": "payment-pool-exhaustion",
        "llm_model": "claude-sonnet-4-20250514",
        "config_source": "defaults",
        "stages": {
            "detection": {
                "is_incident": True,
                "severity": "high",
                "signals": ["s1", "s2", "s3"],
            },
            "diagnosis": {
                "root_cause": "Connection pool exhaustion after config deploy reduced max_connections from 200 to 50",
                "evidence": [
                    "8,432 connection timeouts in 10 minutes",
                    "Deploy at 14:19 changed max_connections: 200 -> 50",
                ],
                "confidence": "high",
                "affected_services": ["payment-svc"],
            },
            "mitigation": {
                "pattern_matched": "connection pool",
                "action": None,
            },
            "verification": {
                "result": "success",
                "health_delta": "+75",
                "message": "Health improved after mitigation",
            },
        },
        "timeline": [
            {"time": "14:19:00", "from": "IDLE", "to": "DETECTING", "reason": "Anomalies detected"},
            {"time": "14:19:01", "from": "DETECTING", "to": "DIAGNOSING", "reason": "Incident confirmed"},
            {"time": "14:19:05", "from": "DIAGNOSING", "to": "MITIGATING", "reason": "Root cause found"},
            {"time": "14:19:06", "from": "MITIGATING", "to": "VERIFYING", "reason": "Action applied"},
            {"time": "14:19:07", "from": "VERIFYING", "to": "RESOLVED", "reason": "Health improved"},
        ],
    }


@pytest.fixture
def sample_incident():
    """Incidente minimo para testes de postmortem."""
    from dataclasses import dataclass, field

    @dataclass
    class FakeIncident:
        deploy_time: datetime = field(default_factory=lambda: datetime(2026, 3, 6, 14, 19, 0))
        spike_start: datetime = field(default_factory=lambda: datetime(2026, 3, 6, 14, 23, 0))
        detection_time: datetime = field(default_factory=lambda: datetime(2026, 3, 6, 14, 25, 0))
        fix_time: datetime = field(default_factory=lambda: datetime(2026, 3, 6, 14, 28, 0))

    return FakeIncident()


@pytest.fixture
def mock_orchestrator():
    """Orchestrator mock para testes de postmortem e summary."""
    orch = MagicMock()
    orch.history = [
        {"from": "IDLE", "to": "DETECTING"},
        {"from": "DETECTING", "to": "DIAGNOSING"},
    ]
    return orch


# --- CLI Parser Tests ---

class TestCliParser:
    """Testes do argumento --tracker no CLI."""

    def test_tracker_jira(self):
        """--tracker jira e aceito."""
        parser = build_arg_parser()
        args = parser.parse_args(["--tracker", "jira"])
        assert args.tracker == "jira"

    def test_tracker_none(self):
        """--tracker none desativa tracking explicitamente."""
        parser = build_arg_parser()
        args = parser.parse_args(["--tracker", "none"])
        assert args.tracker == "none"

    def test_tracker_default_is_none(self):
        """Sem --tracker, default e None (backward-compatible)."""
        parser = build_arg_parser()
        args = parser.parse_args([])
        assert args.tracker is None

    def test_tracker_invalid_raises(self):
        """--tracker invalido gera erro."""
        parser = build_arg_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--tracker", "pagerduty"])

    def test_tracker_with_scenario(self):
        """--tracker jira funciona com --scenario."""
        parser = build_arg_parser()
        args = parser.parse_args(["--tracker", "jira", "--scenario", "memory-leak"])
        assert args.tracker == "jira"
        assert args.scenario == "memory-leak"

    def test_tracker_with_no_llm(self):
        """--tracker jira funciona com --no-llm."""
        parser = build_arg_parser()
        args = parser.parse_args(["--tracker", "jira", "--no-llm"])
        assert args.tracker == "jira"
        assert args.no_llm is True

    def test_tracker_with_output_format(self):
        """--tracker jira funciona com --output-format json."""
        parser = build_arg_parser()
        args = parser.parse_args(["--tracker", "jira", "--output-format", "json"])
        assert args.tracker == "jira"
        assert args.output_format == "json"


# --- Tracker Registry Tests ---

class TestTrackerRegistry:
    """Testes do registry e factory de trackers."""

    def test_jira_in_registry(self):
        """jira esta registrado no TRACKER_REGISTRY."""
        assert "jira" in TRACKER_REGISTRY

    def test_resolve_none_returns_none(self):
        """_resolve_tracker(None) retorna None."""
        assert _resolve_tracker(None) is None

    def test_resolve_none_string_returns_none(self):
        """_resolve_tracker('none') retorna None."""
        assert _resolve_tracker("none") is None

    def test_resolve_unknown_returns_none(self, capsys):
        """_resolve_tracker('pagerduty') retorna None com warning."""
        result = _resolve_tracker("pagerduty")
        assert result is None

    @patch.dict("os.environ", {
        "JIRA_URL": "https://test.atlassian.net",
        "JIRA_PROJECT_KEY": "OPS",
        "JIRA_EMAIL": "test@example.com",
        "JIRA_API_TOKEN": "test-token-123",
    })
    def test_resolve_jira_returns_tracker(self):
        """_resolve_tracker('jira') retorna JiraIncidentTracker quando env vars presentes."""
        from tools.jira_tracker import JiraIncidentTracker
        tracker = _resolve_tracker("jira")
        assert isinstance(tracker, JiraIncidentTracker)
        assert tracker.url == "https://test.atlassian.net"

    @patch.dict("os.environ", {}, clear=True)
    def test_resolve_jira_missing_config_returns_none(self):
        """_resolve_tracker('jira') retorna None com warning quando env vars ausentes."""
        result = _resolve_tracker("jira")
        assert result is None


# --- Step Tracking Tests ---

class TestStepTracking:
    """Testes do step_tracking() — criacao de incidente no tracker."""

    def test_creates_incident_with_correct_args(self, sample_result):
        """step_tracking chama create_incident com titulo, severidade e descricao corretos."""
        tracker = MagicMock()
        tracker.create_incident.return_value = "OPS-123"
        tracker.url = "https://test.atlassian.net"

        issue_key = step_tracking(sample_result, tracker)

        assert issue_key == "OPS-123"
        tracker.create_incident.assert_called_once()
        call_args = tracker.create_incident.call_args
        assert "Connection pool exhaustion" in call_args[0][0]  # title
        assert call_args[0][1] == "high"  # severity
        assert "8,432 connection timeouts" in call_args[0][2]  # description

    def test_returns_none_on_tracker_error(self, sample_result):
        """step_tracking retorna None quando tracker levanta excecao."""
        tracker = MagicMock()
        tracker.create_incident.side_effect = Exception("Connection refused")
        tracker.url = "https://test.atlassian.net"

        issue_key = step_tracking(sample_result, tracker)

        assert issue_key is None

    def test_title_truncated_to_100_chars(self, sample_result):
        """Titulo e truncado a 100 caracteres."""
        sample_result["stages"]["diagnosis"]["root_cause"] = "x" * 200
        tracker = MagicMock()
        tracker.create_incident.return_value = "OPS-456"
        tracker.url = "https://test.atlassian.net"

        step_tracking(sample_result, tracker)

        call_args = tracker.create_incident.call_args
        assert len(call_args[0][0]) == 100

    def test_default_severity_when_missing(self, sample_result):
        """Usa 'medium' como severidade default quando detection nao tem severity."""
        del sample_result["stages"]["detection"]["severity"]
        tracker = MagicMock()
        tracker.create_incident.return_value = "OPS-789"
        tracker.url = "https://test.atlassian.net"

        step_tracking(sample_result, tracker)

        call_args = tracker.create_incident.call_args
        assert call_args[0][1] == "medium"


# --- Postmortem Attachment Tests ---

class TestPostmortemAttachment:
    """Testes do _attach_postmortem_to_tracker()."""

    def test_calls_create_postmortem_with_mapped_timeline(self, sample_result):
        """_attach_postmortem_to_tracker mapeia timeline e chama create_postmortem."""
        tracker = MagicMock()
        tracker.create_postmortem.return_value = "10001"

        comment_id = _attach_postmortem_to_tracker(tracker, "OPS-123", sample_result)

        assert comment_id == "10001"
        tracker.create_postmortem.assert_called_once()
        call_args = tracker.create_postmortem.call_args

        # incident_id
        assert call_args[0][0] == "OPS-123"

        # timeline — deve ter 5 entradas mapeadas
        timeline = call_args[0][1]
        assert len(timeline) == 5
        assert timeline[0]["time"] == "14:19:00"
        assert "IDLE -> DETECTING" in timeline[0]["event"]

        # root_cause
        assert "Connection pool exhaustion" in call_args[0][2]

        # action_items — pattern 'connection pool' tem 5 items
        action_items = call_args[0][3]
        assert len(action_items) == 5
        assert "connection pool" in action_items[0].lower()

    def test_returns_none_on_error(self, sample_result):
        """_attach_postmortem_to_tracker retorna None quando tracker falha."""
        tracker = MagicMock()
        tracker.create_postmortem.side_effect = Exception("API error")

        result = _attach_postmortem_to_tracker(tracker, "OPS-123", sample_result)

        assert result is None

    def test_uses_default_action_items_when_no_mitigation(self, sample_result):
        """Usa action items 'default' quando mitigation esta ausente."""
        sample_result["stages"]["mitigation"] = {}
        tracker = MagicMock()
        tracker.create_postmortem.return_value = "10002"

        _attach_postmortem_to_tracker(tracker, "OPS-123", sample_result)

        call_args = tracker.create_postmortem.call_args
        action_items = call_args[0][3]
        assert len(action_items) > 0  # default items existem


# --- Postmortem Markdown Tracking Section Tests ---

class TestPostmortemTrackingSection:
    """Testes da secao ## Incident Tracking no postmortem markdown."""

    def test_tracking_section_included_when_tracker_info_provided(
        self, sample_result, sample_incident, mock_orchestrator, tmp_path
    ):
        """Postmortem markdown inclui ## Incident Tracking quando tracker_issue_key presente."""
        md_path, _ = step_postmortem(
            sample_result, sample_incident,
            output_dir=str(tmp_path),
            output_format="markdown",
            tracker_url="https://test.atlassian.net",
            tracker_issue_key="OPS-123",
        )

        content = md_path.read_text()
        assert "## Incident Tracking" in content
        assert "OPS-123" in content
        assert "https://test.atlassian.net/browse/OPS-123" in content

    def test_tracking_section_excluded_when_no_tracker(
        self, sample_result, sample_incident, mock_orchestrator, tmp_path
    ):
        """Postmortem markdown NAO inclui ## Incident Tracking sem tracker."""
        md_path, _ = step_postmortem(
            sample_result, sample_incident,
            output_dir=str(tmp_path),
            output_format="markdown",
        )

        content = md_path.read_text()
        assert "## Incident Tracking" not in content

    def test_json_output_includes_tracking_stage(
        self, sample_result, sample_incident, mock_orchestrator, tmp_path
    ):
        """JSON output inclui 'tracking' quando presente em result['stages']."""
        sample_result["stages"]["tracking"] = {
            "issue_key": "OPS-123",
            "url": "https://test.atlassian.net",
        }

        _, json_path = step_postmortem(
            sample_result, sample_incident,
            output_dir=str(tmp_path),
            output_format="json",
            tracker_url="https://test.atlassian.net",
            tracker_issue_key="OPS-123",
        )

        import json
        data = json.loads(json_path.read_text())
        assert "tracking" in data
        assert data["tracking"]["issue_key"] == "OPS-123"

    def test_json_output_excludes_tracking_when_not_present(
        self, sample_result, sample_incident, mock_orchestrator, tmp_path
    ):
        """JSON output NAO inclui 'tracking' quando nao ha tracker."""
        _, json_path = step_postmortem(
            sample_result, sample_incident,
            output_dir=str(tmp_path),
            output_format="json",
        )

        import json
        data = json.loads(json_path.read_text())
        assert "tracking" not in data


# --- Print Summary Tests ---

class TestPrintSummaryTracker:
    """Testes de tracker_info no print_summary()."""

    def test_summary_includes_tracker_row(self, sample_result, mock_orchestrator, capsys):
        """print_summary inclui linha de Tracking quando tracker_info presente."""
        tracker_info = {"issue_key": "OPS-123", "url": "https://test.atlassian.net"}

        print_summary(sample_result, tracker_info=tracker_info)

        output = capsys.readouterr().out
        assert "OPS-123" in output

    def test_summary_shows_skipped_on_tracker_failure(self, sample_result, mock_orchestrator, capsys):
        """print_summary mostra 'skipped' quando tracker falhou."""
        tracker_info = {"issue_key": None, "url": None}

        print_summary(sample_result, tracker_info=tracker_info)

        output = capsys.readouterr().out
        assert "skipped" in output

    def test_summary_no_tracker_row_when_none(self, sample_result, mock_orchestrator, capsys):
        """print_summary nao inclui linha de Tracking quando tracker_info e None."""
        print_summary(sample_result, tracker_info=None)

        output = capsys.readouterr().out
        assert "Tracking" not in output
