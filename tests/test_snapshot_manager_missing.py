"""Snapshot ausente falha em vez de se criar e passar (T-g).

P3-8 (docs/analise-20261109.md): SnapshotManager criava o arquivo quando ele
não existia e retornava. No CI, um snapshot esquecido no commit (ou apagado)
passava sempre, comparando a saída com ela mesma.
"""
from __future__ import annotations

import pytest

from tests.snapshot_utils import SnapshotManager

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("method,suffix,data", [
    ("assert_match", ".json", {"a": 1}),
    ("assert_text_match", ".txt", "texto"),
])
def test_missing_snapshot_fails_and_is_not_created(tmp_path, method, suffix, data):
    manager = SnapshotManager(tmp_path, update=False)
    with pytest.raises(AssertionError, match="--snapshot-update"):
        getattr(manager, method)(data, "novo")
    assert not (tmp_path / f"novo{suffix}").exists()


@pytest.mark.parametrize("method,suffix,data", [
    ("assert_match", ".json", {"a": 1}),
    ("assert_text_match", ".txt", "texto"),
])
def test_snapshot_update_creates_then_matches(tmp_path, method, suffix, data):
    getattr(SnapshotManager(tmp_path, update=True), method)(data, "novo")
    assert (tmp_path / f"novo{suffix}").exists()
    getattr(SnapshotManager(tmp_path, update=False), method)(data, "novo")
