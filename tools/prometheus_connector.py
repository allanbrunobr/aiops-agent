"""Conector Prometheus (HTTP API) — implementa MetricsSource.

``query()`` usa ``GET /api/v1/query_range``; ``get_alerts()`` usa
``GET /api/v1/alerts``. Somente leitura (nenhuma escrita/infra).

Timeout e retries (com backoff exponencial) em falhas transitórias:
timeout, erro de conexão e HTTP 5xx/429. Erros 4xx (ex.: PromQL inválido)
NÃO sofrem retry. Base URL via ``PROMETHEUS_URL``.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from application.ports.telemetry_sources import MetricsSource

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_SERVICE_LABELS = ("service", "app", "pod", "job", "instance")


@dataclass
class PrometheusConnectorError(Exception):
    """Erro estruturado (espelha DatabricksConnectorError)."""

    service: str
    error_type: str  # config | timeout | connection | http | parse
    timestamp: str
    details: str

    def __post_init__(self) -> None:
        super().__init__(self.details)

    def __str__(self) -> str:
        return f"[{self.error_type}] {self.service} at {self.timestamp}: {self.details}"


class PrometheusMetricsSource(MetricsSource):
    """MetricsSource sobre a HTTP API do Prometheus."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        backoff_seconds: float = 0.5,
    ) -> None:
        url = base_url or os.environ.get("PROMETHEUS_URL")
        if not url:
            raise self._error("config", "Missing base_url (or PROMETHEUS_URL env var)")
        self.base_url = url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self.backoff_seconds = backoff_seconds

    # -- infra ---------------------------------------------------------------

    def _error(self, error_type: str, details: str) -> PrometheusConnectorError:
        return PrometheusConnectorError(
            service=self.__class__.__name__,
            error_type=error_type,
            timestamp=datetime.now().isoformat(),
            details=details,
        )

    def _get(self, path: str, params: dict | None = None) -> dict:
        """GET com timeout + retries; devolve o JSON (status=success)."""
        last: PrometheusConnectorError | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(self.backoff_seconds * 2 ** (attempt - 1))
            try:
                resp = requests.get(
                    self.base_url + path, params=params, timeout=self.timeout_seconds
                )
            except requests.Timeout as e:
                last = self._error("timeout", f"GET {path} timed out: {e}")
                continue
            except requests.ConnectionError as e:
                last = self._error("connection", f"GET {path} connection failed: {e}")
                continue
            except requests.RequestException as e:
                raise self._error("http", f"GET {path} failed: {e}") from e

            if resp.status_code in _RETRYABLE_STATUS:
                last = self._error("http", f"GET {path} returned {resp.status_code}")
                continue
            if resp.status_code >= 400:
                raise self._error(
                    "http", f"GET {path} returned {resp.status_code}: {resp.text[:200]}"
                )
            try:
                body = resp.json()
            except ValueError as e:
                raise self._error("parse", f"Invalid JSON from {path}: {e}") from e
            if not isinstance(body, dict) or body.get("status") != "success":
                raise self._error(
                    "http", f"Prometheus error on {path}: {str(body)[:200]}"
                )
            return body
        assert last is not None
        raise last

    # -- MetricsSource -------------------------------------------------------

    def query(
        self, promql: str, start: datetime, end: datetime, step_seconds: int = 15
    ) -> list[dict]:
        """query_range → ``[{timestamp(iso), service, metric, value}]``.

        Amostras NaN/Inf/não numéricas são descartadas (Prometheus emite
        "NaN" como string); não viram 0.0 para não fabricar sinal.
        """
        body = self._get(
            "/api/v1/query_range",
            {
                "query": promql,
                "start": _epoch(start),
                "end": _epoch(end),
                "step": step_seconds,
            },
        )
        try:
            series = body["data"]["result"]
        except (KeyError, TypeError) as e:
            raise self._error("parse", f"Unexpected payload: {e}") from e

        out: list[dict] = []
        for s in series:
            labels = s.get("metric", {})
            service = next(
                (labels[k] for k in _SERVICE_LABELS if labels.get(k)), "unknown"
            )
            name = labels.get("__name__") or promql
            for ts, raw in s.get("values", []):
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(value):
                    continue
                out.append(
                    {
                        "timestamp": datetime.fromtimestamp(
                            float(ts), timezone.utc
                        ).isoformat(),
                        "service": str(service),
                        "metric": str(name),
                        "value": value,
                    }
                )
        return out

    def get_alerts(self, active_only: bool = True) -> list[dict]:
        """``/api/v1/alerts`` → ``[{alert, service, severity, status}]``."""
        body = self._get("/api/v1/alerts")
        try:
            alerts = body["data"]["alerts"]
        except (KeyError, TypeError) as e:
            raise self._error("parse", f"Unexpected payload: {e}") from e
        out = []
        for a in alerts:
            state = a.get("state", "")
            if active_only and state != "firing":
                continue
            labels = a.get("labels", {})
            out.append(
                {
                    "alert": labels.get("alertname", ""),
                    "service": next(
                        (labels[k] for k in _SERVICE_LABELS if labels.get(k)), "unknown"
                    ),
                    "severity": labels.get("severity", "unknown"),
                    "status": state,
                }
            )
        return out


def _epoch(dt: datetime) -> float:
    """datetime → epoch s; naive é tratado como UTC (consistente com o CSV)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()
