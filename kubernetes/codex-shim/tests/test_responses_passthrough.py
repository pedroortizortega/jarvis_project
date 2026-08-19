"""Tests for `/v1/responses` (Amendment 5): LiteLLM bridges Chat Completions
calls carrying both `reasoning_effort` and `tools` for gpt-5.4+ models
directly to `{api_base}/responses` — bypassing `/v1/chat/completions`
entirely (litellm/main.py's `responses_api_bridge_check`). Hermes sends both
on every agentic turn. Unlike chat_completions, no translation is needed:
this is a near-direct proxy — swap auth, forward the body, forward the
response back byte-for-byte.
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
                "id": "resp_abc",
                "status": "completed",
                "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
                # Matches the real account behavior confirmed live: the
                # terminal event's own `output` is genuinely empty; the
                # actual items only ever arrive via `response.output_item.done`.
                "output": [],
            },
        },
    ]
    return "".join(f"data: {json.dumps(ev)}\n\n" for ev in events).encode()


def build_app(fake_core_v1, upstream_handler=None, captured_requests=None):
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

    def default_handler(request: httpx.Request) -> httpx.Response:
        if captured_requests is not None:
            captured_requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=httpx.ByteStream(_upstream_sse_body()),
        )

    upstream_transport = httpx.MockTransport(upstream_handler or default_handler)
    http_client = httpx.AsyncClient(transport=upstream_transport)

    os.environ["CODEX_SHIM_INTERNAL_KEY"] = INTERNAL_KEY
    return create_app(session_manager=manager, http_client=http_client)


def _real_responses_request_body() -> dict:
    # Shaped exactly like what LiteLLM's Responses-API bridge actually sends
    # — already Responses-API-native, no messages[]/chat-completions shape.
    return {
        "model": "gpt-5.6-sol",
        "input": [{"role": "user", "content": "ping"}],
        "instructions": "You are a helpful assistant.",
        "reasoning": {"effort": "medium"},
        "tools": [],
        "store": False,
    }


def test_nonstreaming_responses_passthrough_returns_raw_upstream_object():
    fake_core_v1 = FakeCoreV1Api()
    app = build_app(fake_core_v1)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/responses",
                headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
                json=dict(_real_responses_request_body(), stream=False),
            )

    resp = asyncio.run(_run())

    assert resp.status_code == 200
    body = resp.json()
    # Raw upstream Responses object, byte-for-byte semantics — not a
    # chat.completion shape.
    assert body["id"] == "resp_abc"
    assert body["status"] == "completed"
    assert body["output"][0]["content"][0]["text"] == "pong"


def test_upstream_always_asked_to_stream_even_when_caller_did_not():
    fake_core_v1 = FakeCoreV1Api()
    captured: list = []
    app = build_app(fake_core_v1, captured_requests=captured)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/responses",
                headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
                json=dict(_real_responses_request_body(), stream=False),
            )

    asyncio.run(_run())

    assert len(captured) == 1
    assert captured[0]["stream"] is True


def test_upstream_always_forced_store_false_even_when_litellm_sends_store_true():
    """Regression test found live (Amendment 5): the real upstream rejects
    any `store` value other than `false` (`{"detail":"Store must be set to
    false"}`). LiteLLM's Responses-API bridge sets its own `store` value
    with no knowledge of this account-specific constraint, so it must be
    force-overridden here the same way `stream` already is."""
    fake_core_v1 = FakeCoreV1Api()
    captured: list = []
    app = build_app(fake_core_v1, captured_requests=captured)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/responses",
                headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
                json=dict(_real_responses_request_body(), store=True),
            )

    asyncio.run(_run())

    assert len(captured) == 1
    assert captured[0]["store"] is False


def test_streaming_responses_passthrough_forwards_raw_sse_unchanged():
    fake_core_v1 = FakeCoreV1Api()
    app = build_app(fake_core_v1)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            async with client.stream(
                "POST",
                "/v1/responses",
                headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
                json=dict(_real_responses_request_body(), stream=True),
            ) as response:
                assert response.status_code == 200
                raw = b""
                async for chunk in response.aiter_bytes():
                    raw += chunk
                return raw

    raw_bytes = asyncio.run(_run())

    # Byte-for-byte passthrough: the exact upstream SSE body, untranslated.
    assert raw_bytes == _upstream_sse_body()


def test_max_output_tokens_and_temperature_stripped_before_upstream():
    """Regression test found live (Amendment 5): this account's Codex
    endpoint rejects `max_output_tokens` and `temperature` outright
    (`{"detail":"Unsupported parameter: max_output_tokens"}`) — matches
    Hermes's own known-good client's documented constraint. LiteLLM's
    Responses-API bridge translates the caller's `max_tokens` into
    `max_output_tokens` with no knowledge of this, so both must be stripped
    here regardless of what LiteLLM sends."""
    fake_core_v1 = FakeCoreV1Api()
    captured: list = []
    app = build_app(fake_core_v1, captured_requests=captured)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/responses",
                headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
                json=dict(_real_responses_request_body(), max_output_tokens=10, temperature=0.7),
            )

    asyncio.run(_run())

    assert len(captured) == 1
    assert "max_output_tokens" not in captured[0]
    assert "temperature" not in captured[0]


def test_nonstreaming_splices_collected_items_into_empty_output():
    """Regression test found live (Amendment 5): the real terminal
    `response.completed` event's `response.output` is genuinely empty for
    this account — LiteLLM's own Responses-API client rejects that shape
    ("Unknown items in responses API response: []"). The items collected
    from `response.output_item.done` events during the stream must be
    spliced back into the returned `output` field."""
    fake_core_v1 = FakeCoreV1Api()
    app = build_app(fake_core_v1)  # default handler emits _upstream_sse_body(), output: [] on terminal

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/responses",
                headers={"Authorization": f"Bearer {INTERNAL_KEY}"},
                json=dict(_real_responses_request_body(), stream=False),
            )

    resp = asyncio.run(_run())

    body = resp.json()
    assert body["output"], "expected the spliced-in items, got empty output"
    assert body["output"][0]["content"][0]["text"] == "pong"


def test_missing_internal_bearer_rejected():
    fake_core_v1 = FakeCoreV1Api()
    app = build_app(fake_core_v1)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/v1/responses", json=_real_responses_request_body())

    resp = asyncio.run(_run())
    assert resp.status_code == 401
