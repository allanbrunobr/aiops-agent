"""Cliente A2A para o DiagnosisAgent chamar agentes remotos.

Implementa o protocolo A2A v0.3 (Linux Foundation):
  - discover(): lê o Agent Card do agente remoto
  - send_task(): envia task JSON-RPC 2.0 e aguarda resultado síncrono

Requer httpx >= 0.27 para chamadas async.
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx
import structlog

log = structlog.get_logger()


class A2AClient:
    """Chama agentes remotos via protocolo A2A.

    Exemplo de uso:
        client = A2AClient(token="Bearer eyJ...")
        card = await client.discover("https://other-agent.example.com")
        result = await client.send_task(
            agent_url="https://other-agent.example.com",
            skill_id="diagnose_incident",
            data={"source": "pagerduty", "severity": "high", ...},
        )
    """

    def __init__(
        self,
        token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Inicializa o cliente A2A.

        Args:
            token: Bearer token JWT para autenticação (opcional se o agente alvo for público).
            timeout: Timeout em segundos para requisições (padrão 30s).
        """
        self._token = token
        self._timeout = timeout

    async def discover(self, agent_url: str) -> dict:
        """Lê o Agent Card do agente remoto.

        Args:
            agent_url: URL base do agente (ex: "https://other-agent.example.com").

        Returns:
            Dict com o Agent Card (name, description, skills, securitySchemes).

        Raises:
            httpx.HTTPError: Falha de rede ou HTTP.
        """
        card_url = f"{agent_url.rstrip('/')}/a2a/agent-card.json"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(card_url, headers=self._auth_headers())
            response.raise_for_status()
            card: dict = response.json()
            log.info("a2a.client.discovered", agent_url=agent_url, name=card.get("name"))
            return card

    async def send_task(
        self,
        agent_url: str,
        skill_id: str,
        data: dict[str, Any],
        task_id: str | None = None,
    ) -> dict:
        """Envia uma task A2A ao agente remoto e retorna o resultado.

        Usa JSON-RPC 2.0 com método tasks/send.

        Args:
            agent_url: URL base do agente remoto.
            skill_id: Identificador da skill a invocar (ex: "diagnose_incident").
            data: Dados a enviar na task (payload da skill).
            task_id: ID da task (gerado automaticamente se None).

        Returns:
            Dict com 'result' contendo id, status e artifacts da task.

        Raises:
            httpx.HTTPError: Falha de rede ou HTTP.
            ValueError: Agente retornou erro JSON-RPC.
        """
        effective_task_id = task_id or str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "method": "tasks/send",
            "id": "1",
            "params": {
                "id": effective_task_id,
                "skill": {"id": skill_id},
                "message": {
                    "role": "user",
                    "parts": [{"type": "data", "data": data}],
                },
            },
        }

        tasks_url = f"{agent_url.rstrip('/')}/a2a/tasks"
        log.info(
            "a2a.client.sending",
            agent_url=agent_url,
            skill_id=skill_id,
            task_id=effective_task_id,
        )

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                tasks_url,
                json=payload,
                headers=self._auth_headers(),
            )
            response.raise_for_status()
            body: dict = response.json()

        if "error" in body:
            err = body["error"]
            raise ValueError(
                f"A2A error {err.get('code')}: {err.get('message')}"
            )

        result = body.get("result", {})
        log.info(
            "a2a.client.completed",
            task_id=effective_task_id,
            status=result.get("status", {}).get("state"),
        )
        return result

    def _auth_headers(self) -> dict[str, str]:
        """Retorna headers de autenticação se token configurado."""
        if self._token:
            token = self._token
            if not token.startswith("Bearer "):
                token = f"Bearer {token}"
            return {"Authorization": token}
        return {}
