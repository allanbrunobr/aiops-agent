"""Conector Jira para tracking de incidentes AIOps.

Implementa a interface IncidentTracker (tools/interfaces.py) via
Jira Cloud REST API v2. Cria issues para incidentes detectados e
adiciona comentários de postmortem com timeline, root cause e action items.

Autenticação via HTTP Basic Auth (email + API token) seguindo o
padrão Atlassian Cloud. Configuração lida de variáveis de ambiente
ou passada explicitamente no construtor.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from tools.interfaces import IncidentTracker


# Mapeamento de severidade do diagnóstico para prioridade Jira
SEVERITY_PRIORITY_MAP = {
    "critical": "Highest",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}


@dataclass
class JiraTrackerError(Exception):
    """Erro estruturado para falhas de conexão Jira."""

    service: str
    error_type: str
    timestamp: str
    details: str

    def __post_init__(self) -> None:
        """Inicializa Exception.args para compatibilidade com traceback."""
        super().__init__(self.details)

    def __str__(self) -> str:
        """Representação textual do erro."""
        return (
            f"[{self.error_type}] {self.service} at {self.timestamp}: "
            f"{self.details}"
        )


class JiraIncidentTracker(IncidentTracker):
    """Tracker de incidentes via Jira Cloud REST API v2.

    Implementa a interface IncidentTracker criando issues para
    incidentes detectados e comentários para postmortems.
    Lazy-import de `requests` para evitar custo de startup
    quando o tracker não é utilizado.
    """

    def __init__(
        self,
        url: str | None = None,
        project_key: str | None = None,
        email: str | None = None,
        api_token: str | None = None,
    ) -> None:
        """Inicializa tracker Jira.

        Parâmetros podem ser passados explicitamente ou lidos de
        variáveis de ambiente: JIRA_URL, JIRA_PROJECT_KEY,
        JIRA_EMAIL, JIRA_API_TOKEN.
        """
        self.url = (url or os.environ.get("JIRA_URL", "")).rstrip("/")
        self.project_key = project_key or os.environ.get("JIRA_PROJECT_KEY")
        self.email = email or os.environ.get("JIRA_EMAIL")
        self.api_token = api_token or os.environ.get("JIRA_API_TOKEN")
        self._validate_config()

    def _validate_config(self) -> None:
        """Valida que todas as configurações obrigatórias estão presentes."""
        missing = []
        if not self.url:
            missing.append("url (or JIRA_URL env var)")
        if not self.project_key:
            missing.append("project_key (or JIRA_PROJECT_KEY env var)")
        if not self.email:
            missing.append("email (or JIRA_EMAIL env var)")
        if not self.api_token:
            missing.append("api_token (or JIRA_API_TOKEN env var)")

        if missing:
            raise JiraTrackerError(
                service="JiraIncidentTracker",
                error_type="config",
                timestamp=datetime.now().isoformat(),
                details=(
                    "Missing required configuration: "
                    + ", ".join(missing)
                ),
            )

    def _get_auth(self) -> tuple[str, str]:
        """Retorna credenciais para HTTP Basic Auth (Jira Cloud)."""
        return (self.email, self.api_token)

    def _post(self, endpoint: str, payload: dict) -> dict:
        """Executa POST na Jira REST API com tratamento padronizado.

        Centraliza lazy-import de requests, autenticação, timeout,
        tratamento de erros HTTP e parsing de JSON. Todas as exceções
        são convertidas em JiraTrackerError estruturado.
        """
        import requests

        try:
            response = requests.post(
                endpoint,
                json=payload,
                auth=self._get_auth(),
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
        except requests.ConnectionError as e:
            raise JiraTrackerError(
                service="JiraIncidentTracker",
                error_type="network",
                timestamp=datetime.now().isoformat(),
                details=f"Connection failed: {e}",
            ) from e
        except requests.Timeout as e:
            raise JiraTrackerError(
                service="JiraIncidentTracker",
                error_type="network",
                timestamp=datetime.now().isoformat(),
                details=f"Request timed out: {e}",
            ) from e
        except Exception as e:
            raise JiraTrackerError(
                service="JiraIncidentTracker",
                error_type="network",
                timestamp=datetime.now().isoformat(),
                details=f"Request failed: {e}",
            ) from e

        if not response.ok:
            raise JiraTrackerError(
                service="JiraIncidentTracker",
                error_type=self._classify_http_error(response.status_code),
                timestamp=datetime.now().isoformat(),
                details=(
                    f"Jira API returned {response.status_code}: "
                    f"{response.text}"
                ),
            )

        try:
            return response.json()
        except Exception as e:
            raise JiraTrackerError(
                service="JiraIncidentTracker",
                error_type="api",
                timestamp=datetime.now().isoformat(),
                details=f"Invalid JSON in Jira response: {e}",
            ) from e

    def create_incident(
        self, title: str, severity: str, description: str
    ) -> str:
        """Cria issue Jira para um incidente detectado.

        Envia POST para /rest/api/2/issue com campos mapeados:
        summary, description, labels, priority e issue type.
        Retorna a chave da issue criada (ex: 'OPS-123').
        """
        priority = SEVERITY_PRIORITY_MAP.get(
            severity.lower(), "Medium"
        )

        payload = {
            "fields": {
                "project": {"key": self.project_key},
                "summary": title,
                "description": description,
                "issuetype": {"name": "Bug"},
                "priority": {"name": priority},
                "labels": ["aiops-generated"],
            }
        }

        endpoint = f"{self.url}/rest/api/2/issue"
        data = self._post(endpoint, payload)
        return data["key"]

    def create_postmortem(
        self,
        incident_id: str,
        timeline: list[dict],
        root_cause: str,
        action_items: list[str],
    ) -> str:
        """Adiciona comentário de postmortem a uma issue Jira existente.

        Formata timeline, root cause e action items em Jira wiki markup
        e envia como comentário na issue identificada por incident_id.
        Retorna o ID do comentário criado.
        """
        body = self._format_postmortem(timeline, root_cause, action_items)
        payload = {"body": body}
        endpoint = f"{self.url}/rest/api/2/issue/{incident_id}/comment"
        data = self._post(endpoint, payload)
        return str(data["id"])

    @staticmethod
    def _format_postmortem(
        timeline: list[dict],
        root_cause: str,
        action_items: list[str],
    ) -> str:
        """Formata postmortem em Jira wiki markup.

        Usa sintaxe wiki do Jira: h3. para headers, * para bullets,
        # para listas numeradas.
        """
        parts = []

        # Header
        parts.append("h3. Postmortem Report")
        parts.append("")

        # Root cause
        parts.append("h4. Root Cause")
        parts.append(root_cause)
        parts.append("")

        # Timeline
        if timeline:
            parts.append("h4. Timeline")
            for entry in timeline:
                time = entry.get("time", entry.get("timestamp", "?"))
                event = entry.get("event", entry.get("description", "?"))
                parts.append(f"* *{time}*: {event}")
            parts.append("")

        # Action items
        if action_items:
            parts.append("h4. Action Items")
            for item in action_items:
                parts.append(f"# {item}")
            parts.append("")

        return "\n".join(parts)

    @staticmethod
    def _classify_http_error(status_code: int) -> str:
        """Classifica código HTTP em tipo de erro estruturado."""
        if status_code in (401, 403):
            return "auth"
        if status_code == 404:
            return "not_found"
        return "api"
