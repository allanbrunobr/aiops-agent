"""Token não-ASCII no header de autenticação: 401, não 500.

O Starlette decodifica headers em latin-1, e `hmac.compare_digest` com `str`
não-ASCII levanta TypeError. Um `Authorization: Bearer é` virava erro 500
antes da autenticação, nas três fontes do webhook (analise-20261109, 4B).
"""
from __future__ import annotations

import pytest

from application.ports.credential_provider import CredentialProvider

pytestmark = pytest.mark.unit


class _Creds(CredentialProvider):
    def get(self, key: str) -> str | None:
        return "s3cret-token"


@pytest.mark.parametrize(("cls", "headers"), [
    ("GrafanaNormalizer", {"authorization": "Bearer té"}),
    ("DatabricksNormalizer", {"authorization": "Bearer té"}),
    ("PagerDutyNormalizer", {"x-pagerduty-signature": "v1=éé"}),
])
def test_non_ascii_credential_is_rejected_not_raised(cls, headers):
    import interfaces.http.webhook.normalizers.databricks as databricks
    import interfaces.http.webhook.normalizers.grafana as grafana
    import interfaces.http.webhook.normalizers.pagerduty as pagerduty

    normalizer = next(getattr(m, cls) for m in (grafana, databricks, pagerduty) if hasattr(m, cls))
    assert normalizer().validate_auth(headers, b"{}", _Creds()) is False


def test_matching_secret_still_matches():
    from interfaces.http.webhook.normalizers.base import secrets_match

    assert secrets_match("s3cret-token", "s3cret-token") is True
    assert secrets_match("s3cret-tokeN", "s3cret-token") is False


def test_router_answers_401_for_non_ascii_bearer():
    from fastapi.testclient import TestClient

    from tests.test_webhook_router import _app

    resp = TestClient(_app()).post(
        "/webhook", content=b"{}",
        headers={"Alert-Source": "grafana", "Content-Type": "application/json",
                 "Authorization": "Bearer té".encode("latin-1")},
    )
    assert resp.status_code == 401
