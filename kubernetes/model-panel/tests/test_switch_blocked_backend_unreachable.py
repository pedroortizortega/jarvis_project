"""Phase 13 / task 6.1 & 13.1: D17 fail-closed regression for the new
`backend_unreachable` session state (F-1, design "Verified Findings").

`ALLOWED_SESSION_STATES = {"valid", "expiring_soon"}` in
`app/clients/codex_shim.py` is an allow-list, so `backend_unreachable` (and
the panel's own synthetic `unreachable`, used when codex-shim itself can't
be reached at all) are rejected by construction — this file asserts that
with **zero code changes** to `codex_shim.py`, confirming the allow-list
still needs no edit for this change to be safe, and that
`assert_switch_to_cloud_allowed` performs zero cluster calls before
raising.
"""

from __future__ import annotations

import pytest

from app.clients.codex_shim import (
    ALLOWED_SESSION_STATES,
    SwitchBlocked,
    assert_switch_to_cloud_allowed,
)


class _FailIfCalled:
    """Any attribute access is a bug: `assert_switch_to_cloud_allowed` must
    never reach a cluster client when the session state itself already
    disqualifies the switch — it should raise from the session-status
    check alone."""

    def __getattr__(self, name):  # pragma: no cover - defensive
        raise AssertionError(f"unexpected cluster call: .{name}")


class _StubShimClient:
    def __init__(self, state, reason=None):
        self._state = state
        self._reason = reason

    def get_session_status(self):
        return {"state": self._state, "reason": self._reason, "expires_at": None}


@pytest.mark.parametrize("state", ["backend_unreachable", "unreachable"])
def test_new_states_are_excluded_by_the_existing_allow_list_zero_edit(state):
    assert state not in ALLOWED_SESSION_STATES


@pytest.mark.parametrize("state", ["backend_unreachable", "unreachable"])
def test_switch_blocked_raised_with_zero_cluster_calls(state):
    client = _StubShimClient(state, reason="kubernetes API secret read failed (k8s_api_500)")
    cluster_client = _FailIfCalled()  # would raise AssertionError if ever touched

    with pytest.raises(SwitchBlocked) as excinfo:
        assert_switch_to_cloud_allowed(client)

    assert excinfo.value.session_state == state
    # Sanity: nothing about this path ever consults a cluster client.
    del cluster_client


def test_backend_unreachable_reason_is_surfaced_on_switch_blocked():
    client = _StubShimClient("backend_unreachable", reason="kubernetes API secret read failed (k8s_transport)")
    with pytest.raises(SwitchBlocked) as excinfo:
        assert_switch_to_cloud_allowed(client)
    assert "k8s_transport" in str(excinfo.value)
