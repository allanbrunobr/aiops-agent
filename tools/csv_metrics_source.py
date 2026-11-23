"""MetricsSource offline sobre CSV (``timestamp,service,metric_name,value``).

Formato gravado por ``data.hpa_saturation_scenario.export_csv``. Permite
testar o Vigia Barato sem Prometheus. Somente leitura.
"""

from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from application.ports.telemetry_sources import MetricsSource

_REQUIRED = ("timestamp", "service", "metric_name", "value")
_SELECTOR = re.compile(r"^\s*([A-Za-z_:][A-Za-z0-9_:]*)")


def _aware(dt: datetime) -> datetime:
    """Naive é tratado como UTC, para comparar com janelas aware."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class CsvMetricsSource(MetricsSource):
    """Lê o CSV uma vez (ordenado por timestamp) e responde ``query()``.

    ``promql`` é reduzido ao nome da métrica (antes de ``{``/``(``) e casado
    por igualdade com ``metric_name``; vazio casa tudo. ``step_seconds`` é
    aceito por compatibilidade e NÃO faz downsampling. Sem teto de linhas.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        rows: list[tuple[datetime, dict]] = []
        with self.path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            missing = [c for c in _REQUIRED if c not in (reader.fieldnames or [])]
            if missing:
                raise ValueError(f"CSV sem colunas obrigatórias: {', '.join(missing)}")
            for row in reader:
                ts = datetime.fromisoformat(row["timestamp"])
                rows.append(
                    (
                        _aware(ts),
                        {
                            "timestamp": row["timestamp"],
                            "service": row["service"],
                            "metric": row["metric_name"],
                            "value": float(row["value"]),
                        },
                    )
                )
        rows.sort(key=lambda r: r[0])
        self._rows = rows

    def query(
        self, promql: str, start: datetime, end: datetime, step_seconds: int = 15
    ) -> list[dict]:
        m = _SELECTOR.match(promql or "")
        name = m.group(1) if m else ""
        lo, hi = _aware(start), _aware(end)
        return [
            dict(rec)
            for ts, rec in self._rows
            if lo <= ts <= hi and (not name or rec["metric"] == name)
        ]

    def get_alerts(self, active_only: bool = True) -> list[dict]:
        """CSV não carrega alertas."""
        return []
