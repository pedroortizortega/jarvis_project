"""Regression test for a bug found by /code-review (Amendment 5): a passive
`ensure_fresh()` caller (model-panel's 2s status poll, via `/internal/session`)
re-attempted a live OAuth refresh on EVERY call once the cached token entered
the skew window. When the upstream refresh kept failing (rate limit, network
blip) without advancing `expires_at`, every subsequent 2s poll retried again,
indefinitely — pure monitoring traffic hammering the token endpoint and
worsening any real rate-limiting. `ensure_fresh()` must back off after a
failed proactive attempt instead of retrying on every call.
"""

from __future__ import annotations

import asyncio
import functools

import httpx

from app import codex_auth
from app.session import SessionManager
from app.store import TokenStore
from tests.conftest import jwt_with_exp


def make_manager(fake_core_v1, responder, *, clock, min_retry_interval=30):
    store = TokenStore(k8s_core_v1=fake_core_v1)
    # Token already inside the 120s skew window from t=0.
    access = jwt_with_exp(60.0)
    fake_core_v1.seed(
        "codex-shim-auth",
        "llms",
        {"access_token": access, "refresh_token": "rt-initial"},
    )
    transport = httpx.MockTransport(responder)
    refresh_fn = functools.partial(codex_auth.refresh_codex_oauth_pure, transport=transport)
    return SessionManager(
        store=store,
        refresh_fn=refresh_fn,
        skew_seconds=120,
        clock=clock,
        min_retry_interval_seconds=min_retry_interval,
    )


def test_repeated_ensure_fresh_polls_do_not_hammer_a_failing_refresh(fake_core_v1):
    call_count = 0

    def always_fails(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, headers={"retry-after": "30"}, json={})

    now = [0.0]
    manager = make_manager(fake_core_v1, always_fails, clock=lambda: now[0], min_retry_interval=30)

    # Simulate three 2-second status polls, all within the 30s backoff window.
    # Real callers (e.g. main.py's /internal/session) catch AuthError and
    # keep polling status(); mirror that here.
    for _ in range(3):
        try:
            asyncio.run(manager.ensure_fresh())
        except codex_auth.AuthError:
            pass
        now[0] += 2.0

    assert call_count == 1, (
        f"expected exactly one live refresh attempt across three polls inside "
        f"the backoff window, got {call_count} — passive polling must not "
        "hammer a failing upstream"
    )
    assert manager.status()["state"] == "rate_limited"


def test_ensure_fresh_retries_again_after_backoff_window_elapses(fake_core_v1):
    call_count = 0

    def always_fails(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(429, headers={"retry-after": "30"}, json={})

    now = [0.0]
    manager = make_manager(fake_core_v1, always_fails, clock=lambda: now[0], min_retry_interval=30)

    for delay in (0.0, 31.0):  # second poll is past the backoff window
        now[0] += delay
        try:
            asyncio.run(manager.ensure_fresh())
        except codex_auth.AuthError:
            pass

    assert call_count == 2, "a poll after the backoff window elapses must retry"
