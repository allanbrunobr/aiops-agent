from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger()


class LocalFallbackWriter:
    """Escritor de fallback local para postmortems quando Slack esta indisponivel.

    Garante que nenhum diagnostico e perdido silenciosamente.
    Grava JSON estruturado no diretorio output/.
    """

    def __init__(self, fallback_dir: str = "output") -> None:
        """Inicializa com diretorio de fallback."""
        self._dir = Path(fallback_dir)

    def write(self, correlation_id: str, postmortem_data: dict[str, Any]) -> Path:
        """Grava postmortem como JSON no diretorio de fallback.

        Retorna o caminho do arquivo criado.
        """
        self._dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        filename = f"fallback_{correlation_id}_{timestamp}.json"
        filepath = self._dir / filename

        payload = {
            "correlation_id": correlation_id,
            "fallback_reason": "slack_delivery_exhausted",
            "written_at": datetime.utcnow().isoformat() + "Z",
            "postmortem": postmortem_data,
        }

        filepath.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

        log.warning(
            "notification.fallback_local",
            correlation_id=correlation_id,
            filepath=str(filepath),
        )

        return filepath
