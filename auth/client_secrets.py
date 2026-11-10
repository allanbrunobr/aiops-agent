"""Hashes dos client secrets OAuth lidos de um arquivo fora do repositório.

O `config/defaults.yaml` é versionado e não deve carregar `secret_sha256`.
`mcp.oauth.client_secrets_file` (env: AIOPS_MCP__OAUTH__CLIENT_SECRETS_FILE)
aponta para um JSON `{client_id: sha256 hex}` — só hashes, nunca o segredo.

Fail-closed: arquivo ausente, ilegível, malformado ou legível por grupo/outros
resulta em nenhum hash, e nenhum cliente sem hash recebe token.
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_log = logging.getLogger(__name__)


def load_client_secret_hashes(path: str) -> dict[str, str]:
    """Lê `{client_id: sha256 hex}` de `path`. Qualquer problema => {}."""
    if not path:
        return {}
    try:
        mode = os.stat(path).st_mode
    except OSError as exc:
        _log.warning("OAuth client secrets file unavailable (%s): %s", path, exc)
        return {}
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        _log.warning(
            "OAuth client secrets file %s is accessible by group/others (mode %o); "
            "ignoring it — use chmod 600", path, stat.S_IMODE(mode),
        )
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        _log.warning("OAuth client secrets file %s is not valid JSON: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        _log.warning("OAuth client secrets file %s must be a JSON object", path)
        return {}
    hashes = {
        str(client_id): value.lower()
        for client_id, value in data.items()
        if isinstance(value, str) and _SHA256_HEX.match(value.lower())
    }
    dropped = len(data) - len(hashes)
    if dropped:
        _log.warning("OAuth client secrets file %s: %d invalid entries ignored", path, dropped)
    return hashes
