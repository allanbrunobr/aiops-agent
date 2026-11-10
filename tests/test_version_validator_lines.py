"""VersionValidator: patch releases de uma linha vulnerável também são vulneráveis.

P2 — F08 (docs/analise-20261109.md): as chaves de KNOWN_VULNERABLE_VERSIONS
são linhas de release ("9.4", "1.16", "6.2"), mas a comparação era de
igualdade exata — `postgres:9.4.26` e `nginx:1.16.1` passavam como SAFE.
A comparação é por componentes (não por prefixo de string, que fazia "1"
herdar o CVE de "1.16").
"""
from __future__ import annotations

import pytest

from domain.incident import MitigationAction
from safety.version_validator import VersionValidator


def _safe(command: str) -> bool:
    action = MitigationAction(description="set image", command=command, risk_level="LOW")
    return VersionValidator().validate_mitigation_action(action).is_safe


@pytest.mark.unit
class TestReleaseLineMatching:
    @pytest.mark.parametrize("image", [
        "postgres:9.4.26", "nginx:1.16.1", "redis:6.2.14", "redis:5.0.7", "node:14.21.3",
    ])
    def test_patch_release_of_vulnerable_line_is_flagged(self, image):
        assert _safe(f"kubectl set image deployment/app app={image}") is False

    @pytest.mark.parametrize("image", ["postgres:9.4", "nginx:1.16"])
    def test_exact_vulnerable_line_still_flagged(self, image):
        assert _safe(f"kubectl set image deployment/app app={image}") is False

    @pytest.mark.parametrize("image", [
        "postgres:9.40.1",  # componente 40 ≠ 4 (não é prefixo de string)
        "nginx:1.24.0",
        "redis:7.2.4",
        "postgres:16.2",
    ])
    def test_other_lines_are_not_flagged(self, image):
        assert _safe(f"kubectl set image deployment/app app={image}") is True
