"""Environment-backed credential provider adapter."""

from __future__ import annotations

import os

from application.ports.credential_provider import CredentialProvider

__all__ = ["EnvCredentialProvider"]


class EnvCredentialProvider(CredentialProvider):
    """CredentialProvider implementation that reads values from environment variables."""

    def get(self, key: str) -> str | None:
        """Return the credential value for an environment variable key."""
        return os.environ.get(key)
