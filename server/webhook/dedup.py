from __future__ import annotations

import hashlib
import time

import structlog

from core.models import NormalizedAlert

log = structlog.get_logger()

# Intervalo minimo entre limpezas de entradas expiradas (em segundos)
_CLEANUP_INTERVAL = 60.0


class DedupStore:
    """Armazenamento in-memory para deduplicacao de alertas com TTL.

    Usa um dicionario de fingerprints com timestamps de expiracao.
    Entradas expiradas sao limpas periodicamente para evitar
    crescimento ilimitado de memoria.
    """

    def __init__(self) -> None:
        # fingerprint -> (correlation_id, expires_at_monotonic)
        self._store: dict[str, tuple[str, float]] = {}
        self._last_cleanup: float = time.monotonic()

    def is_duplicate(self, fingerprint: str) -> tuple[bool, str | None]:
        """Verifica se o fingerprint ja foi visto dentro da janela TTL.

        Args:
            fingerprint: Hash SHA-256 do alerta.

        Returns:
            Tupla (is_duplicate, original_correlation_id).
            Se nao for duplicata, correlation_id e None.
        """
        self._maybe_cleanup()

        entry = self._store.get(fingerprint)
        if entry is None:
            return False, None

        original_cid, expires_at = entry
        if time.monotonic() > expires_at:
            # Entrada expirada — nao e duplicata
            del self._store[fingerprint]
            return False, None

        return True, original_cid

    def record(
        self,
        fingerprint: str,
        correlation_id: str,
        ttl_seconds: float,
    ) -> None:
        """Registra um fingerprint com TTL.

        Args:
            fingerprint: Hash SHA-256 do alerta.
            correlation_id: ID de correlacao do alerta original.
            ttl_seconds: Tempo de vida em segundos.
        """
        expires_at = time.monotonic() + ttl_seconds
        self._store[fingerprint] = (correlation_id, expires_at)
        log.debug(
            "dedup.recorded",
            fingerprint=fingerprint[:16],
            correlation_id=correlation_id,
            ttl_seconds=ttl_seconds,
        )

    def _maybe_cleanup(self) -> None:
        """Remove entradas expiradas se passou tempo suficiente desde ultima limpeza."""
        now = time.monotonic()
        if now - self._last_cleanup < _CLEANUP_INTERVAL:
            return

        expired_keys = [
            k for k, (_, expires_at) in self._store.items()
            if now > expires_at
        ]
        for k in expired_keys:
            del self._store[k]

        if expired_keys:
            log.debug("dedup.cleanup", removed=len(expired_keys), remaining=len(self._store))

        self._last_cleanup = now

    @property
    def size(self) -> int:
        """Numero de entradas no store (inclui possivelmente expiradas)."""
        return len(self._store)

    def clear(self) -> None:
        """Limpa todo o store — uso exclusivo em testes."""
        self._store.clear()


def generate_fingerprint(alert: NormalizedAlert) -> str:
    """Gera fingerprint deterministico para deduplicacao.

    O fingerprint e baseado em campos que identificam unicamente
    um alerta: fonte, ID da fonte, titulo e severidade.

    Args:
        alert: Alerta normalizado.

    Returns:
        Hash SHA-256 hexadecimal (64 caracteres).
    """
    content = f"{alert.source}|{alert.source_id}|{alert.title}|{alert.severity}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
