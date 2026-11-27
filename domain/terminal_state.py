"""Estado terminal honesto do pipeline (B5, AIOPS-76).

Função pura que decide (rótulo terminal, success, exit code) a partir do TNR
e da decisão de segurança. ``RESOLVED`` só é devolvido quando há verificação
MEDIDA com sucesso, ou quando a ação proposta é read-only auto-aprovada pela
política (``safety_verdict == "APPROVED"``). Qualquer outra combinação com
ação proposta pendente fica em ``AWAITING_APPROVAL`` (success=False, exit 3):
"resolvido" sem verificação medida é falso-verificado.

Precedência: regressão/rollback/erro (exit 2) > aprovação pendente (exit 3)
> resolvido (exit 0).
"""

from __future__ import annotations

from dataclasses import dataclass

# Modos de verificação que contam como medição real. O TNR simulado
# (``verification_mode="simulated"``) nunca executa infra real (SECURITY.md).
MEASURED_MODES = frozenset({"measured", "real"})

# Resultados de TNR que indicam que a ação piorou/falhou (exit 2).
TNR_FAILURE_RESULTS = frozenset({"regression", "rollback_complete", "error"})

EXIT_SUCCESS = 0
EXIT_REGRESSION = 2
EXIT_APPROVAL_NEEDED = 3


@dataclass(frozen=True)
class TerminalOutcome:
    state: str  # RESOLVED | AWAITING_APPROVAL | FAILED
    success: bool
    exit_code: int


def is_measured(verification_mode: str | None) -> bool:
    return verification_mode in MEASURED_MODES


def classify_terminal(
    *,
    tnr_result: str | None,
    verification_mode: str | None,
    has_proposed_action: bool,
    auto_approved: bool,
    regression: bool = False,
    needs_review: bool = False,
) -> TerminalOutcome:
    """Decide o estado terminal. ``auto_approved`` = ação read-only aprovada."""
    if regression or tnr_result in TNR_FAILURE_RESULTS:
        return TerminalOutcome("FAILED", False, EXIT_REGRESSION)
    if needs_review:
        # PreValidator mandou para revisão humana: nunca resolvido sozinho.
        return TerminalOutcome("AWAITING_APPROVAL", False, EXIT_APPROVAL_NEEDED)
    if not has_proposed_action:
        return TerminalOutcome("RESOLVED", True, EXIT_SUCCESS)
    if tnr_result == "success" and is_measured(verification_mode):
        return TerminalOutcome("RESOLVED", True, EXIT_SUCCESS)
    if auto_approved:
        return TerminalOutcome("RESOLVED", True, EXIT_SUCCESS)
    return TerminalOutcome("AWAITING_APPROVAL", False, EXIT_APPROVAL_NEEDED)
