"""RED tests 2.1-2.5 (Phase 2): refresh classification, rotation persistence,
single-flight. Target: app.session.SessionManager (Phase 3, task 3.1)."""

from __future__ import annotations

import asyncio
import functools
import time

import httpx
import pytest

from app import codex_auth
from app.store import TokenStore
from tests.conftest import jwt_with_exp, mock_token_transport


def make_manager(fake_core_v1, responder, *, skew_seconds=120, seed_expires_in=3600):
    store = TokenStore(k8s_core_v1=fake_core_v1)
    access = jwt_with_exp(time.time() + seed_expires_in)
    fake_core_v1.seed(
        "codex-shim-auth",
        "llms",
        {"access_token": access, "refresh_token": "rt-initial"},
    )
    transport = mock_token_transport(responder)
    refresh_fn = functools.partial(codex_auth.refresh_codex_oauth_pure, transport=transport)

    from app.session import SessionManager

    return SessionManager(store=store, refresh_fn=refresh_fn, skew_seconds=skew_seconds)


def test_refresh_maps_429_to_rate_limited(fake_core_v1):
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "30"}, json={})

    manager = make_manager(fake_core_v1, responder)

    with pytest.raises(codex_auth.AuthError):
        asyncio.run(manager.refresh())

    assert manager.status()["state"] == "rate_limited"


def test_refresh_maps_terminal_errors_to_expired_needs_relogin(fake_core_v1):
    cases = [
        (400, {"error": "invalid_grant", "error_description": "bad"}),
        (400, {"error": "refresh_token_reused"}),
        (401, {}),
        (403, {}),
    ]
    for status_code, body in cases:
        def responder(request: httpx.Request, _status=status_code, _body=body) -> httpx.Response:
            return httpx.Response(_status, json=_body)

        manager = make_manager(fake_core_v1, responder)
        with pytest.raises(codex_auth.AuthError):
            asyncio.run(manager.refresh())
        assert manager.status()["state"] == "expired_needs_relogin", (status_code, body)


def test_refresh_maps_5xx_to_refresh_failed(fake_core_v1):
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "temporarily_unavailable"})

    manager = make_manager(fake_core_v1, responder)
    with pytest.raises(codex_auth.AuthError):
        asyncio.run(manager.refresh())

    assert manager.status()["state"] == "refresh_failed"


def test_rotated_refresh_token_persisted(fake_core_v1):
    def responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "access_token": jwt_with_exp(time.time() + 3600),
                "refresh_token": "rt-rotated-new",
            },
        )

    manager = make_manager(fake_core_v1, responder)
    asyncio.run(manager.refresh())

    assert fake_core_v1.patch_calls, "expected a Secret patch call"
    last_patch = fake_core_v1.patch_calls[-1]
    patched_data = last_patch["body"]["data"]
    import base64

    assert base64.b64decode(patched_data["refresh_token"]).decode() == "rt-rotated-new"


def test_single_flight_refresh(fake_core_v1):
    call_count = {"n": 0}

    def responder(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        time.sleep(0.05)
        return httpx.Response(
            200,
            json={
                "access_token": jwt_with_exp(time.time() + 3600),
                "refresh_token": f"rt-{call_count['n']}",
            },
        )

    manager = make_manager(fake_core_v1, responder)

    retry_count = {"n": 0}

    async def call_fn(access_token: str):
        retry_count["n"] += 1
        return access_token

    async def one_401():
        return await manager.handle_401_and_retry(call_fn)

    async def run_concurrent():
        return await asyncio.gather(*(one_401() for _ in range(5)))

    results = asyncio.run(run_concurrent())

    assert call_count["n"] == 1, "expected exactly one refresh call across all concurrent 401s"
    assert retry_count["n"] == 5, "expected exactly one retry per concurrent caller"
    assert all(results)
