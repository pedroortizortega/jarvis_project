"""Regression test for a bug found during live cluster verification (Amendment 4):
the Responses API rejects a request with `stream` omitted/false for this
account/model combination — `{"detail":"Stream must be set to true"}` — even
when the shim's *caller* asked for a non-streaming Chat Completions response.
The shim must always request `stream: true` from upstream and, when the
caller wanted non-streaming, buffer/collapse the SSE into one assembled
`chat.completion` object rather than forwarding the caller's `stream` value
verbatim into the upstream request.
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


def _upstream_sse_body() -> bytes:
    # Mirrors the real API (confirmed live, design.md Amendment 4): the
    # message item arrives via `response.output_item.done` DURING the
    # stream; the terminal `response.completed`'s own `response.output` is
    # genuinely empty. The shim must accumulate items as they arrive.
    events = [
        {"type": "response.output_text.delta", "delta": "pong"},
        {
            "type": "response.output_item.done",
            "item": {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "pong"}],
            },
        },
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
                "output": [],
            },
        },
    ]
    return "".join(f"data: {json.dumps(ev)}\n\n" for ev in events).encode()


def build_app_capturing_upstream_body(fake_core_v1, captured: list):
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
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=httpx.ByteStream(_upstream_sse_body()),
        )

    upstream_transport = httpx.MockTransport(upstream_handler)
    http_client = httpx.AsyncClient(transport=upstream_transport)

    os.environ["CODEX_SHIM_INTERNAL_KEY"] = INTERNAL_KEY
    return create_app(session_manager=manager, http_client=http_client)


def test_nonstreaming_caller_still_sends_stream_true_upstream():
    fake_core_v1 = FakeCoreV1Api()
    captured_upstream_requests: list = []
    app = build_app_capturing_upstream_body(fake_core_v1, captured_upstream_requests)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
                json={
                    "model": "cloud",
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False,
                },
            )

    resp = asyncio.run(_run())

    assert len(captured_upstream_requests) == 1, "expected exactly one upstream call"
    assert captured_upstream_requests[0].get("stream") is True, (
        "upstream Responses API call must always set stream=true, even for a "
        f"non-streaming caller; got {captured_upstream_requests[0]!r}"
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "pong"
