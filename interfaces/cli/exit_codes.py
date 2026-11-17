"""Exit codes da CLI — contrato público documentado no README (Story 3.2).

Compartilhado pelos dois engines (V0 sequencial e V1 Blueprint).
"""

from __future__ import annotations

EXIT_SUCCESS = 0  # Pipeline resolved (TNR success)
EXIT_ERROR = 1  # Unrecoverable error (exception in any stage) or invalid args
EXIT_REGRESSION = 2  # TNR detected regression (post-health worse)
EXIT_APPROVAL_NEEDED = 3  # Safety blocked action (human approval required)

EXIT_CODE_LABELS: dict[int, str] = {
    EXIT_SUCCESS: "resolved",
    EXIT_ERROR: "error",
    EXIT_REGRESSION: "regression",
    EXIT_APPROVAL_NEEDED: "approval-needed",
}

__all__ = [
    "EXIT_SUCCESS",
    "EXIT_ERROR",
    "EXIT_REGRESSION",
    "EXIT_APPROVAL_NEEDED",
    "EXIT_CODE_LABELS",
]
