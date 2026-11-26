"""B6: post_state_fn/rollback_fn que levantam não escapam nem viram sucesso."""

import pytest

from safety.tnr import TNRResult, TNRSafetyGuard
from tests.test_safety import _make_health_snapshot, _make_mitigation_action

pytestmark = pytest.mark.unit


def _run(post_state_fn, rollback_fn=None):
    guard = TNRSafetyGuard()
    report = guard.execute_with_safety(
        _make_mitigation_action(),
        _make_health_snapshot(),
        execute_fn=lambda a: None,
        post_state_fn=post_state_fn,
        rollback_fn=rollback_fn,
    )
    return guard, report


def _regressed():
    return _make_health_snapshot(error_rate=90.0, availability=10.0)


def test_post_state_fn_raises_is_error():
    def boom():
        raise RuntimeError("metrics down")

    guard, report = _run(boom)
    assert report.result == TNRResult.ERROR
    assert "metrics down" in report.message
    assert guard.history[-1] is report


def test_rollback_fn_raises_is_not_rollback_complete():
    def bad_rollback(action):
        raise RuntimeError("kubectl failed")

    guard, report = _run(_regressed, bad_rollback)
    assert report.result == TNRResult.ERROR
    assert report.result != TNRResult.ROLLBACK_COMPLETE
    assert "kubectl failed" in report.message
    assert guard.history[-1] is report


def test_rollback_fn_ok_still_rollback_complete():
    calls = []
    _, report = _run(_regressed, lambda a: calls.append(a))
    assert report.result == TNRResult.ROLLBACK_COMPLETE
    assert len(calls) == 1
