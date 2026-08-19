"""RED (6.5): fail-closed on non-valid Codex session (D17)."""
from __future__ import annotations

import pytest

from app.clients.codex_shim import SwitchBlocked, assert_switch_to_cloud_allowed


class _StubShimClient:
    def __init__(self, status=None, raises=None):
        self._status = status
        self._raises = raises
        self.calls = 0

    def get_session_status(self):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._status


@pytest.mark.parametrize(
    "state",
    ["not_configured", "expired_needs_relogin", "refresh_failed", "rate_limited", None],
)
def test_non_valid_session_blocks_switch(state):
    client = _StubShimClient(status={"state": state, "reason": "x"})
    with pytest.raises(SwitchBlocked):
        assert_switch_to_cloud_allowed(client)


@pytest.mark.parametrize("state", ["valid", "expiring_soon"])
def test_valid_or_expiring_soon_allows_switch(state):
    client = _StubShimClient(status={"state": state})
    assert_switch_to_cloud_allowed(client)  # must not raise


def test_shim_unreachable_blocks_switch():
    client = _StubShimClient(raises=ConnectionError("timeout"))
    with pytest.raises(SwitchBlocked):
        assert_switch_to_cloud_allowed(client)
