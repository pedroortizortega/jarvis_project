"""Regression tests for a bug found by /code-review (Amendment 5): neither
the non-streaming nor the streaming `/v1/chat/completions` call site caught
`AuthError`/`SecretNotFound` from `ensure_fresh()`/`handle_401_and_retry()`.
A client whose refresh genuinely failed (rate-limited, needs re-login) got a
generic unhandled-exception 500 with no indication of what actually
happened, instead of a structured error a caller (LiteLLM, the panel) could
act on.
"""

from __future__ import annotations

import functools
import json
import os
import time

import httpx
from fastapi.testclient import TestClient

from app import codex_auth
from app.main import create_app
from app.session import SessionManager
from app.store import TokenStore
from tests.conftest import FakeCoreV1Api, jwt_with_exp, mock_token_transport

INTERNAL_KEY = "test-internal-key"


def build_app_with_failing_refresh(status_code: int, body: dict):
    """A token already inside the skew window, whose refresh always fails
    with the given upstream response."""
    fake_core_v1 = FakeCoreV1Api()
    store = TokenStore(k8s_core_v1=fake_core_v1)
    fake_core_v1.seed(
        "codex-shim-auth",
        "llms",
        {"access_token": jwt_with_exp(time.time() + 5), "refresh_token": "rt"},
    )

    def token_responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    transport = mock_token_transport(token_responder)
    refresh_fn = functools.partial(codex_auth.refresh_codex_oauth_pure, transport=transport)
    manager = SessionManager(store=store, refresh_fn=refresh_fn, skew_seconds=120)

    os.environ["CODEX_SHIM_INTERNAL_KEY"] = INTERNAL_KEY
    return create_app(session_manager=manager)


def test_nonstreaming_rate_limited_refresh_surfaces_429_not_500():
    app = build_app_with_failing_refresh(429, {})

    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
            json={"model": "cloud", "messages": [{"role": "user", "content": "hi"}], "stream": False},
        )

    assert resp.status_code == 429, resp.text
    assert resp.json()["error"]["state"] == "rate_limited"


def test_nonstreaming_expired_refresh_surfaces_401_not_500():
    app = build_app_with_failing_refresh(400, {"error": "invalid_grant"})

    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
            json={"model": "cloud", "messages": [{"role": "user", "content": "hi"}], "stream": False},
        )

    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["state"] == "expired_needs_relogin"


def test_streaming_expired_refresh_surfaces_structured_error_not_crash():
    app = build_app_with_failing_refresh(400, {"error": "invalid_grant"})

    with TestClient(app) as client:
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
            json={"model": "cloud", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        )

    # The proactive ensure_fresh() call at the top of the streaming branch
    # runs before any bytes are sent, so a proactive failure here still
    # surfaces as a normal structured JSON response, not a broken stream.
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["state"] == "expired_needs_relogin"
