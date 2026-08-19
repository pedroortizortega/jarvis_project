"""Regression test for a bug found by /code-review (Amendment 5): a
standalone `type: "error"` SSE event (no terminal response.completed/failed
event following it) was silently ignored by the non-streaming `_consume()`
loop. `final_response` stayed `None` and the caller got a generic 502
"Upstream stream ended without a terminal event" instead of the real error
code/message the streaming path already surfaces via the same
`_error_from_event` check.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import time

import httpx

from app import codex_auth
from app.main import create_app
from app.session import SessionManager
from app.store import TokenStore
from tests.conftest import FakeCoreV1Api, jwt_with_exp, mock_token_transport

INTERNAL_KEY = "test-internal-key"


def _upstream_error_only_body() -> bytes:
    event = {"type": "error", "code": "content_policy_violation", "message": "blocked by policy"}
    return f"data: {json.dumps(event)}\n\n".encode()


def build_app(fake_core_v1):
    store = TokenStore(k8s_core_v1=fake_core_v1)
    fake_core_v1.seed(
        "codex-shim-auth",
        "llms",
        {"access_token": jwt_with_exp(time.time() + 3600), "refresh_token": "rt"},
    )

    def token_responder(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": jwt_with_exp(time.time() + 3600)})

    transport = mock_token_transport(token_responder)
    refresh_fn = functools.partial(codex_auth.refresh_codex_oauth_pure, transport=transport)
    manager = SessionManager(store=store, refresh_fn=refresh_fn)

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=httpx.ByteStream(_upstream_error_only_body()),
        )

    upstream_transport = httpx.MockTransport(upstream_handler)
    http_client = httpx.AsyncClient(transport=upstream_transport)

    os.environ["CODEX_SHIM_INTERNAL_KEY"] = INTERNAL_KEY
    return create_app(session_manager=manager, http_client=http_client)


def test_standalone_error_event_surfaces_real_error_not_generic_502():
    fake_core_v1 = FakeCoreV1Api()
    app = build_app(fake_core_v1)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
                json={"model": "cloud", "messages": [{"role": "user", "content": "ping"}], "stream": False},
            )

    resp = asyncio.run(_run())

    body = resp.json()
    # UpstreamResponseError's own default status_code is 502 (matches the
    # streaming path's convention for this same error class) — the bug was
    # never about the status code, it was that the real message/code from
    # the standalone `error` event was dropped entirely in favor of the
    # unrelated generic "stream ended without a terminal event" message.
    assert body["error"]["message"] == "blocked by policy", body
    assert body["error"]["code"] == "content_policy_violation", body
