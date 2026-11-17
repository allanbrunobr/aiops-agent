"""Regressão F2-5: bypass da allowlist read-only por substring.

Antes do fix, re.search casava o padrão read-only em QUALQUER posição:
'kubectl get pods && python -c ...' passava como read-only e podia chegar
a AUTO_APPROVED. Composição deve ser rejeitada ANTES da allowlist, e a
allowlist deve casar o binário+subcomando reais (shlex), não substrings.
"""

from __future__ import annotations

import pytest


def _check(cmd: str) -> tuple[bool, str]:
    from safety.pre_validator import PreExecutionValidator

    return PreExecutionValidator()._check_command_safety(cmd)


@pytest.mark.unit
class TestAllowlistCompositionBypass:
    def test_readonly_prefix_with_and_python_blocked(self):
        ok, _reason = _check("kubectl get pods && python -c 'print(1)'")
        assert ok is False

    def test_semicolon_blocked(self):
        ok, _reason = _check("kubectl get pods; rm -fr /")
        assert ok is False

    def test_pipe_blocked(self):
        ok, _reason = _check("kubectl get pods | tee out.txt")
        assert ok is False

    def test_or_operator_blocked(self):
        ok, _reason = _check("kubectl get pods || curl http://evil")
        assert ok is False

    def test_newline_blocked(self):
        ok, _reason = _check("kubectl get pods\nrm -fr /")
        assert ok is False

    def test_command_substitution_blocked(self):
        ok, _reason = _check("kubectl get $(echo pods)")
        assert ok is False

    def test_backtick_blocked(self):
        ok, _reason = _check("kubectl get `echo pods`")
        assert ok is False

    def test_readonly_prefix_masks_rollout_blocked(self):
        ok, _reason = _check("kubectl get pods && kubectl rollout undo deployment/x")
        assert ok is False


@pytest.mark.unit
class TestAllowlistStillWorks:
    @pytest.mark.parametrize(
        "cmd",
        [
            "kubectl get pods -n production",
            "kubectl describe deployment/payment-svc",
            "kubectl logs payment-svc-abc",
            "kubectl top pods",
            "curl https://example.com",
            "ping -c 3 payment-svc",
            "nslookup payment-svc",
            "kubectl rollout status deployment/payment-svc",
        ],
    )
    def test_simple_readonly_passes(self, cmd):
        ok, reason = _check(cmd)
        assert ok is True
        assert reason == "Read-only command"

    def test_rollout_undo_still_reversible(self):
        ok, reason = _check("kubectl rollout undo deployment/payment-svc")
        assert ok is True
        assert "Rollout" in reason

    def test_destructive_still_blocked(self):
        assert _check("rm -fr /")[0] is False
        assert _check("find / -delete")[0] is False
        assert _check("kubectl delete namespace production")[0] is False
        assert _check("rm -rf /")[0] is False

    def test_unknown_command_default_deny(self):
        assert _check("python script.py")[0] is False
        assert _check("kubectl apply -f deploy.yaml")[0] is False
