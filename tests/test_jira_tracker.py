"""Testes unitários para JiraIncidentTracker.

Valida criação de issues, postmortem como comentário, tratamento
de erros HTTP, validação de configuração e mapeamento de campos.
Todos os testes mockam requests.post() — sem chamadas HTTP reais.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from tools.jira_tracker import (
    JiraIncidentTracker,
    JiraTrackerError,
    SEVERITY_PRIORITY_MAP,
)


# --- Fixtures ---

@pytest.fixture
def jira_env(monkeypatch):
    """Configura variáveis de ambiente para Jira."""
    monkeypatch.setenv("JIRA_URL", "https://mycompany.atlassian.net")
    monkeypatch.setenv("JIRA_PROJECT_KEY", "OPS")
    monkeypatch.setenv("JIRA_EMAIL", "bot@company.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "test-token-123")


@pytest.fixture
def tracker(jira_env):
    """Retorna JiraIncidentTracker configurado via env vars."""
    return JiraIncidentTracker()


@pytest.fixture
def mock_response_ok():
    """Cria mock de response HTTP 201 com issue key."""
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 201
    resp.json.return_value = {
        "id": "10001",
        "key": "OPS-42",
        "self": "https://mycompany.atlassian.net/rest/api/2/issue/10001",
    }
    return resp


@pytest.fixture
def mock_comment_ok():
    """Cria mock de response HTTP 201 com comment ID."""
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 201
    resp.json.return_value = {
        "id": "90001",
        "self": "https://mycompany.atlassian.net/rest/api/2/issue/OPS-42/comment/90001",
    }
    return resp


# --- TestValidateConfig ---

class TestValidateConfig:
    """Testes para _validate_config()."""

    def test_raises_when_no_config(self):
        """Deve falhar listando todas as variáveis ausentes."""
        env = {k: "" for k in ["JIRA_URL", "JIRA_PROJECT_KEY", "JIRA_EMAIL", "JIRA_API_TOKEN"]}
        with patch.dict(os.environ, env, clear=False):
            for key in ["JIRA_URL", "JIRA_PROJECT_KEY", "JIRA_EMAIL", "JIRA_API_TOKEN"]:
                os.environ.pop(key, None)

            with pytest.raises(JiraTrackerError) as exc_info:
                JiraIncidentTracker()

            error = exc_info.value
            assert error.error_type == "config"
            assert "JIRA_URL" in error.details
            assert "JIRA_PROJECT_KEY" in error.details
            assert "JIRA_EMAIL" in error.details
            assert "JIRA_API_TOKEN" in error.details

    def test_raises_when_partial_config(self, monkeypatch):
        """Deve listar apenas as variáveis ausentes."""
        monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net")
        monkeypatch.setenv("JIRA_PROJECT_KEY", "TEST")
        monkeypatch.delenv("JIRA_EMAIL", raising=False)
        monkeypatch.delenv("JIRA_API_TOKEN", raising=False)

        with pytest.raises(JiraTrackerError) as exc_info:
            JiraIncidentTracker()

        error = exc_info.value
        assert "JIRA_EMAIL" in error.details
        assert "JIRA_API_TOKEN" in error.details
        assert "JIRA_URL" not in error.details

    def test_succeeds_with_all_env_vars(self, jira_env):
        """Deve criar tracker sem erro quando todas as vars estão presentes."""
        tracker = JiraIncidentTracker()
        assert tracker.url == "https://mycompany.atlassian.net"
        assert tracker.project_key == "OPS"
        assert tracker.email == "bot@company.com"
        assert tracker.api_token == "test-token-123"

    def test_constructor_params_override_env(self, jira_env):
        """Parâmetros explícitos têm prioridade sobre env vars."""
        tracker = JiraIncidentTracker(
            url="https://override.atlassian.net",
            project_key="CUSTOM",
            email="custom@test.com",
            api_token="custom-token",
        )
        assert tracker.url == "https://override.atlassian.net"
        assert tracker.project_key == "CUSTOM"
        assert tracker.email == "custom@test.com"
        assert tracker.api_token == "custom-token"

    def test_url_trailing_slash_stripped(self, monkeypatch):
        """URL com trailing slash deve ser normalizada."""
        monkeypatch.setenv("JIRA_URL", "https://test.atlassian.net/")
        monkeypatch.setenv("JIRA_PROJECT_KEY", "OPS")
        monkeypatch.setenv("JIRA_EMAIL", "bot@test.com")
        monkeypatch.setenv("JIRA_API_TOKEN", "token")

        tracker = JiraIncidentTracker()
        assert tracker.url == "https://test.atlassian.net"


# --- TestCreateIncident ---

class TestCreateIncident:
    """Testes para create_incident()."""

    @patch("requests.post")
    def test_sends_correct_payload(self, mock_post, tracker, mock_response_ok):
        """Deve enviar payload com summary, description, labels, priority e issue type."""
        mock_post.return_value = mock_response_ok

        tracker.create_incident(
            title="Connection pool exhaustion in payment-svc",
            severity="high",
            description="Root cause: config change reduced pool from 200 to 50",
        )

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json")

        fields = payload["fields"]
        assert fields["project"]["key"] == "OPS"
        assert fields["summary"] == "Connection pool exhaustion in payment-svc"
        assert fields["description"] == "Root cause: config change reduced pool from 200 to 50"
        assert fields["labels"] == ["aiops-generated"]
        assert fields["issuetype"]["name"] == "Bug"
        assert fields["priority"]["name"] == "High"

    @patch("requests.post")
    def test_returns_issue_key(self, mock_post, tracker, mock_response_ok):
        """Deve retornar a chave da issue criada."""
        mock_post.return_value = mock_response_ok

        result = tracker.create_incident("Test", "high", "desc")

        assert result == "OPS-42"

    @patch("requests.post")
    def test_correct_endpoint(self, mock_post, tracker, mock_response_ok):
        """Deve enviar para /rest/api/2/issue."""
        mock_post.return_value = mock_response_ok

        tracker.create_incident("Test", "high", "desc")

        call_args = mock_post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
        assert url == "https://mycompany.atlassian.net/rest/api/2/issue"

    @patch("requests.post")
    def test_uses_basic_auth(self, mock_post, tracker, mock_response_ok):
        """Deve usar HTTP Basic Auth com email e token."""
        mock_post.return_value = mock_response_ok

        tracker.create_incident("Test", "high", "desc")

        call_kwargs = mock_post.call_args
        auth = call_kwargs.kwargs.get("auth")
        assert auth == ("bot@company.com", "test-token-123")

    @patch("requests.post")
    def test_timeout_is_30_seconds(self, mock_post, tracker, mock_response_ok):
        """Deve enviar timeout=30 nas requisições."""
        mock_post.return_value = mock_response_ok

        tracker.create_incident("Test", "high", "desc")

        call_kwargs = mock_post.call_args
        timeout = call_kwargs.kwargs.get("timeout")
        assert timeout == 30


# --- TestSeverityMapping ---

class TestSeverityMapping:
    """Testes para mapeamento severity → Jira priority."""

    @patch("requests.post")
    def test_critical_maps_to_highest(self, mock_post, tracker, mock_response_ok):
        mock_post.return_value = mock_response_ok
        tracker.create_incident("Test", "critical", "desc")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["fields"]["priority"]["name"] == "Highest"

    @patch("requests.post")
    def test_high_maps_to_high(self, mock_post, tracker, mock_response_ok):
        mock_post.return_value = mock_response_ok
        tracker.create_incident("Test", "high", "desc")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["fields"]["priority"]["name"] == "High"

    @patch("requests.post")
    def test_medium_maps_to_medium(self, mock_post, tracker, mock_response_ok):
        mock_post.return_value = mock_response_ok
        tracker.create_incident("Test", "medium", "desc")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["fields"]["priority"]["name"] == "Medium"

    @patch("requests.post")
    def test_low_maps_to_low(self, mock_post, tracker, mock_response_ok):
        mock_post.return_value = mock_response_ok
        tracker.create_incident("Test", "low", "desc")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["fields"]["priority"]["name"] == "Low"

    @patch("requests.post")
    def test_unknown_severity_defaults_to_medium(self, mock_post, tracker, mock_response_ok):
        mock_post.return_value = mock_response_ok
        tracker.create_incident("Test", "unknown-value", "desc")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["fields"]["priority"]["name"] == "Medium"

    @patch("requests.post")
    def test_case_insensitive_mapping(self, mock_post, tracker, mock_response_ok):
        mock_post.return_value = mock_response_ok
        tracker.create_incident("Test", "CRITICAL", "desc")
        payload = mock_post.call_args.kwargs["json"]
        assert payload["fields"]["priority"]["name"] == "Highest"

    def test_severity_map_has_four_entries(self):
        """Mapa deve ter exatamente 4 entradas."""
        assert len(SEVERITY_PRIORITY_MAP) == 4
        assert set(SEVERITY_PRIORITY_MAP.keys()) == {"critical", "high", "medium", "low"}


# --- TestCreatePostmortem ---

class TestCreatePostmortem:
    """Testes para create_postmortem()."""

    @patch("requests.post")
    def test_sends_formatted_comment(self, mock_post, tracker, mock_comment_ok):
        """Deve enviar comentário formatado em Jira wiki markup."""
        mock_post.return_value = mock_comment_ok

        timeline = [
            {"time": "14:19", "event": "Deploy changed config"},
            {"time": "14:23", "event": "Connection pool exhaustion"},
        ]
        root_cause = "Config change reduced connection pool from 200 to 50"
        action_items = ["Add config validation to CI/CD", "Set up alerts for pool usage"]

        tracker.create_postmortem("OPS-42", timeline, root_cause, action_items)

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json")
        body = payload["body"]

        # Verifica formato wiki markup
        assert "h3. Postmortem Report" in body
        assert "h4. Root Cause" in body
        assert root_cause in body
        assert "h4. Timeline" in body
        assert "* *14:19*: Deploy changed config" in body
        assert "* *14:23*: Connection pool exhaustion" in body
        assert "h4. Action Items" in body
        assert "# Add config validation to CI/CD" in body
        assert "# Set up alerts for pool usage" in body

    @patch("requests.post")
    def test_returns_comment_id(self, mock_post, tracker, mock_comment_ok):
        """Deve retornar o ID do comentário criado."""
        mock_post.return_value = mock_comment_ok

        result = tracker.create_postmortem("OPS-42", [], "root cause", [])

        assert result == "90001"

    @patch("requests.post")
    def test_correct_endpoint(self, mock_post, tracker, mock_comment_ok):
        """Deve enviar para /rest/api/2/issue/{id}/comment."""
        mock_post.return_value = mock_comment_ok

        tracker.create_postmortem("OPS-42", [], "root cause", [])

        call_args = mock_post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url")
        assert url == "https://mycompany.atlassian.net/rest/api/2/issue/OPS-42/comment"

    @patch("requests.post")
    def test_empty_timeline_and_actions(self, mock_post, tracker, mock_comment_ok):
        """Deve funcionar com timeline e action items vazios."""
        mock_post.return_value = mock_comment_ok

        tracker.create_postmortem("OPS-42", [], "Some root cause", [])

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json")
        body = payload["body"]

        assert "h3. Postmortem Report" in body
        assert "Some root cause" in body
        assert "h4. Timeline" not in body
        assert "h4. Action Items" not in body

    @patch("requests.post")
    def test_timeline_with_timestamp_key(self, mock_post, tracker, mock_comment_ok):
        """Deve aceitar timeline com chave 'timestamp' além de 'time'."""
        mock_post.return_value = mock_comment_ok

        timeline = [{"timestamp": "14:30", "description": "Issue resolved"}]

        tracker.create_postmortem("OPS-42", timeline, "root cause", [])

        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs.get("json")
        body = payload["body"]

        assert "* *14:30*: Issue resolved" in body


# --- TestErrorHandling ---

class TestErrorHandling:
    """Testes para tratamento de erros HTTP."""

    @patch("requests.post")
    def test_401_raises_auth_error(self, mock_post, tracker):
        """401 deve gerar JiraTrackerError com error_type='auth'."""
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 401
        resp.text = "Unauthorized"
        mock_post.return_value = resp

        with pytest.raises(JiraTrackerError) as exc_info:
            tracker.create_incident("Test", "high", "desc")

        assert exc_info.value.error_type == "auth"
        assert "401" in exc_info.value.details

    @patch("requests.post")
    def test_403_raises_auth_error(self, mock_post, tracker):
        """403 deve gerar JiraTrackerError com error_type='auth'."""
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 403
        resp.text = "Forbidden"
        mock_post.return_value = resp

        with pytest.raises(JiraTrackerError) as exc_info:
            tracker.create_incident("Test", "high", "desc")

        assert exc_info.value.error_type == "auth"

    @patch("requests.post")
    def test_404_raises_not_found_error(self, mock_post, tracker):
        """404 deve gerar JiraTrackerError com error_type='not_found'."""
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 404
        resp.text = "Not Found"
        mock_post.return_value = resp

        with pytest.raises(JiraTrackerError) as exc_info:
            tracker.create_postmortem("INVALID-99", [], "rc", [])

        assert exc_info.value.error_type == "not_found"

    @patch("requests.post")
    def test_500_raises_api_error(self, mock_post, tracker):
        """500 deve gerar JiraTrackerError com error_type='api'."""
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 500
        resp.text = "Internal Server Error"
        mock_post.return_value = resp

        with pytest.raises(JiraTrackerError) as exc_info:
            tracker.create_incident("Test", "high", "desc")

        assert exc_info.value.error_type == "api"

    @patch("requests.post")
    def test_connection_error_raises_network(self, mock_post, tracker):
        """ConnectionError deve gerar JiraTrackerError com error_type='network'."""
        import requests
        mock_post.side_effect = requests.ConnectionError("Connection refused")

        with pytest.raises(JiraTrackerError) as exc_info:
            tracker.create_incident("Test", "high", "desc")

        assert exc_info.value.error_type == "network"
        assert "Connection" in exc_info.value.details

    @patch("requests.post")
    def test_timeout_raises_network(self, mock_post, tracker):
        """Timeout deve gerar JiraTrackerError com error_type='network'."""
        import requests
        mock_post.side_effect = requests.Timeout("Request timed out")

        with pytest.raises(JiraTrackerError) as exc_info:
            tracker.create_incident("Test", "high", "desc")

        assert exc_info.value.error_type == "network"
        assert "timed out" in exc_info.value.details

    @patch("requests.post")
    def test_postmortem_error_handling_matches_incident(self, mock_post, tracker):
        """create_postmortem() deve ter mesmo padrão de erros que create_incident()."""
        resp = MagicMock()
        resp.ok = False
        resp.status_code = 401
        resp.text = "Unauthorized"
        mock_post.return_value = resp

        with pytest.raises(JiraTrackerError) as exc_info:
            tracker.create_postmortem("OPS-42", [], "rc", [])

        assert exc_info.value.error_type == "auth"

    @patch("requests.post")
    def test_invalid_json_response_raises_api_error(self, mock_post, tracker):
        """Resposta não-JSON do Jira deve gerar JiraTrackerError com error_type='api'."""
        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.json.side_effect = ValueError("No JSON object could be decoded")
        mock_post.return_value = resp

        with pytest.raises(JiraTrackerError) as exc_info:
            tracker.create_incident("Test", "high", "desc")

        assert exc_info.value.error_type == "api"
        assert "Invalid JSON" in exc_info.value.details


# --- TestLazyImport ---

class TestLazyImport:
    """Testes para verificar lazy-import de requests."""

    def test_requests_not_in_module_globals(self):
        """requests não deve ser importado no nível do módulo."""
        import tools.jira_tracker as module

        # Verifica que nenhuma linha no topo do arquivo faz 'import requests'
        source_lines = open(module.__file__).readlines()
        top_level_imports = []
        for line in source_lines:
            stripped = line.strip()
            if stripped.startswith("class ") or stripped.startswith("def "):
                break
            if "import requests" in stripped and not stripped.startswith("#"):
                top_level_imports.append(stripped)

        assert len(top_level_imports) == 0, (
            f"Found top-level 'import requests': {top_level_imports}"
        )


# --- TestJiraTrackerErrorDataclass ---

class TestJiraTrackerErrorDataclass:
    """Testes para a dataclass JiraTrackerError."""

    def test_is_exception(self):
        """JiraTrackerError deve herdar de Exception."""
        error = JiraTrackerError(
            service="test", error_type="test",
            timestamp="2026-01-01", details="test error",
        )
        assert isinstance(error, Exception)

    def test_str_format(self):
        """__str__ deve seguir formato '[type] service at timestamp: details'."""
        error = JiraTrackerError(
            service="JiraIncidentTracker",
            error_type="auth",
            timestamp="2026-03-07T10:00:00",
            details="Authentication failed",
        )
        expected = "[auth] JiraIncidentTracker at 2026-03-07T10:00:00: Authentication failed"
        assert str(error) == expected

    def test_exception_args(self):
        """args[0] deve conter details para compatibilidade com traceback."""
        error = JiraTrackerError(
            service="test", error_type="test",
            timestamp="2026-01-01", details="my error message",
        )
        assert error.args[0] == "my error message"
