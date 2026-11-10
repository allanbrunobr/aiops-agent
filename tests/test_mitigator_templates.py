"""Templates de remediação do MitigationAgent formatam sem erro.

Regressão de NOVO-01 (docs/analise-20261109.md): o template de CPU usava
`{current+2}`, que str.format trata como NOME de campo, não como expressão.
Qualquer causa raiz com "cpu" levantava KeyError('current+2') em propose(),
e o AgentOrchestrator não trata a exceção.
"""
from __future__ import annotations

import pytest

from agents.mitigator import REMEDIATION_PATTERNS, MitigationAgent
from safety.pre_validator import ApprovalStatus, PreExecutionValidator


@pytest.mark.unit
class TestEveryTemplateFormats:
    @pytest.mark.parametrize("key", sorted(REMEDIATION_PATTERNS))
    def test_pattern_formats_through_propose(self, key):
        """Cada padrão, escolhido pela própria chave na causa raiz, gera comando."""
        root_cause = f"{key} issue in payment-svc" if key not in ("default", "unknown") else "weird"
        confidence = "low" if key == "unknown" else "medium"
        out = MitigationAgent().propose({
            "root_cause": root_cause,
            "affected_services": ["payment-svc"],
            "confidence": confidence,
        })
        action = out["action"]
        assert "{" not in action.command and "}" not in action.command
        assert "payment-svc" in action.command
        if action.rollback_command:
            assert "{" not in action.rollback_command


@pytest.mark.unit
class TestCpuPattern:
    def test_cpu_root_cause_scales_up(self):
        out = MitigationAgent().propose({
            "root_cause": "High CPU saturation in payment-svc",
            "affected_services": ["payment-svc"],
            "confidence": "medium",
        })
        assert out["pattern_matched"] == "cpu"
        assert out["action"].command == "kubectl scale deployment/payment-svc --replicas=5"
        assert out["action"].rollback_command == "kubectl scale deployment/payment-svc --replicas=3"

    def test_cpu_command_is_a_reversible_rollout_for_the_gate(self):
        out = MitigationAgent().propose({
            "root_cause": "cpu throttling",
            "affected_services": ["payment-svc"],
            "confidence": "high",
        })
        r = PreExecutionValidator().validate(
            out["action"], {"confidence": "high", "affected_services": ["payment-svc"]})
        assert r.is_safe is True
        assert r.approval_status != ApprovalStatus.BLOCKED
