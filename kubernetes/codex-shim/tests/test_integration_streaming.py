"""Integration test 4.1 (Amendment 2 — re-scoped from SSE-passthrough to
translation-fidelity): a fake upstream emitting chunked Responses SSE must
produce LiteLLM-parseable `chat.completion.chunk` frames on the shim's
`/v1/chat/completions`, streamed incrementally (not buffered into one blob).
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import time

import httpx
import pytest

from app import codex_auth
from app.main import create_app
from app.session import SessionManager
from app.store import TokenStore
from tests.conftest import FakeCoreV1Api, jwt_with_exp, mock_token_transport

INTERNAL_KEY = "test-internal-key"


def _upstream_sse_body() -> bytes:
    events = [
        {"type": "response.output_text.delta", "delta": "Hel"},
        {"type": "response.output_text.delta", "delta": "lo "},
        {"type": "response.output_text.delta", "delta": "world"},
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "usage": {"input_tokens": 3, "output_tokens": 3, "total_tokens": 6},
                "output": [],
            },
        },
    ]
    lines = []
    for ev in events:
        lines.append(f"data: {json.dumps(ev)}\n\n")
    return "".join(lines).encode()


def build_app_with_fake_upstream(fake_core_v1):
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

    body_chunks = [_upstream_sse_body()[i : i + 16] for i in range(0, len(_upstream_sse_body()), 16)]

    def upstream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=httpx.ByteStream(_upstream_sse_body()),
        )

    upstream_transport = httpx.MockTransport(upstream_handler)
    http_client = httpx.AsyncClient(transport=upstream_transport)

    os.environ["CODEX_SHIM_INTERNAL_KEY"] = INTERNAL_KEY
    app = create_app(session_manager=manager, http_client=http_client)

    return app


async def _run_streaming_request(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
            json={
                "model": "cloud",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        ) as response:
            assert response.status_code == 200
            received_frames = []
            async for line in response.aiter_lines():
                if line.startswith("data:") and line.strip() != "data: [DONE]":
                    payload = line[len("data:"):].strip()
                    if payload:
                        received_frames.append(json.loads(payload))
    return received_frames


def test_streaming_translation_not_buffered_into_one_blob():
    fake_core_v1 = FakeCoreV1Api()
    app = build_app_with_fake_upstream(fake_core_v1)

    received_frames = asyncio.run(_run_streaming_request(app))

    assert len(received_frames) >= 2, "expected multiple discrete chunk frames, not one blob"
    for frame in received_frames:
        assert frame["object"] == "chat.completion.chunk"

    text = "".join(
        f["choices"][0]["delta"].get("content", "")
        for f in received_frames
        if f["choices"][0].get("delta", {}).get("content")
    )
    assert text == "Hello world"

    final = received_frames[-1]
    assert final["choices"][0]["finish_reason"] == "stop"
