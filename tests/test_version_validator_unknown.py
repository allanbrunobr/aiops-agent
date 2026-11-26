"""C4: versão desconhecida ≠ segura (ROBUSTNESS_PLAN §5 C4)."""

from __future__ import annotations

import pytest

from safety.version_validator import RemediationRisk, VersionValidator


def _validate(command: str):
    return VersionValidator().validate_remediation(command, "")


@pytest.mark.unit
class TestUnknownVersionNotSafe:
    def test_known_package_unlisted_version_is_not_verifiable(self):
        r = _validate("downgrade postgres 9.99")
        check = r.checks[0]
        assert check.is_safe is False
        assert "não verificável" in check.reason
        assert r.risk == RemediationRisk.CAUTION
        assert any("não verificável" in w for w in r.warnings)

    def test_alias_of_known_package_also_flagged(self):
        r = _validate("downgrade pg 9.99")
        assert r.checks[0].is_safe is False

    def test_unknown_package_is_safe_with_informative_warning(self):
        r = _validate("downgrade fooservice 2.3")
        assert r.checks[0].is_safe is True
        assert r.risk == RemediationRisk.SAFE
        assert r.is_safe is True
        assert any("não avaliado" in w for w in r.warnings)

    def test_listed_vulnerable_version_still_blocked(self):
        r = _validate("downgrade postgres 9.4")
        assert r.risk == RemediationRisk.MALICIOUS
        assert r.is_safe is False
