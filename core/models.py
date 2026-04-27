"""Shim de compatibilidade — re-exporta contratos do pipeline AIOps.

Os contratos canonicos vivem em ``domain/``:
- ``NormalizedAlert`` em ``domain/alert.py``
- ``IncidentState`` em ``domain/incident_state.py``
- ``MitigationAction`` em ``domain/incident.py``

Este modulo preserva o ponto de entrada historico ``from core.models import ...``
para manter os ~50 import sites existentes intactos. Validacao em runtime para
ingestao de webhooks e serializacao JSON para audit logs (NFR18) seguem
funcionando porque os tipos continuam sendo Pydantic BaseModel.
"""

from __future__ import annotations

from domain.alert import NormalizedAlert
from domain.incident import MitigationAction
from domain.incident_state import IncidentState

__all__ = [
    "VALID_ACCURACY_VERDICTS",
    "NormalizedAlert",
    "IncidentState",
    "MitigationAction",
]

# Verditos validos para o endpoint de accuracy (importado por server/api/accuracy.py)
VALID_ACCURACY_VERDICTS = frozenset({"correct", "partially_correct", "incorrect", "false_positive"})
