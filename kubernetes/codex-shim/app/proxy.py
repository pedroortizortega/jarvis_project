"""`/v1/chat/completions` + `/v1/models` — internal-bearer check, credential
swap, Responses call, refresh-and-retry-once wiring (D14/D15).

Wires `session.py` (auth) together with `codex_translate.py` (protocol
translation). No independent tests of its own beyond wiring — the
translation logic and the refresh state machine are unit-tested directly
(2.1-2.11); this module is covered by the Phase 4 integration test (4.1).
"""

from __future__ import annotations

import logging
import os
from typing import Any, AsyncIterator, Dict

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.codex_translate import (
    StreamTranslator,
    UpstreamResponseError,
    assemble_chat_completion,
    build_responses_request,
)
from app.session import SessionManager

logger = logging.getLogger(__name__)

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_CLOUD_MODEL = os.environ.get("CODEX_CLOUD_MODEL", "gpt-5.1-codex")
INTERNAL_KEY_ENV = "CODEX_SHIM_INTERNAL_KEY"


def _codex_headers(access_token: str) -> Dict[str, str]:
    """Headers required to avoid Cloudflare 403s (mirrors
    `_codex_cloudflare_headers` in auxiliary_client.py — same originator
    pinning, same User-Agent shape, same account-id extraction from the
    JWT). See design.md D-OQ4 for the untested egress-IP risk this
    mitigates but does not guarantee."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "codex_cli_rs/0.0.0 (codex-shim)",
        "originator": "codex_cli_rs",
        "Content-Type": "application/json",
    }
    try:
        import base64
        import json as _json

        parts = access_token.split(".")
        if len(parts) >= 2:
            payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = _json.loads(base64.urlsafe_b64decode(payload_b64))
            acct_id = claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
            if isinstance(acct_id, str) and acct_id:
                headers["ChatGPT-Account-ID"] = acct_id
    except Exception:
        pass
    return headers


def _check_internal_bearer(request: Request) -> None:
    expected = os.environ.get(INTERNAL_KEY_ENV, "")
    if not expected:
        # Fail closed: an unset internal key must never mean "open".
        raise HTTPException(status_code=503, detail="codex-shim internal key not configured")
    auth_header = request.headers.get("authorization", "")
    provided = auth_header[len("Bearer "):] if auth_header.startswith("Bearer ") else ""
    if provided != expected:
        raise HTTPException(status_code=401, detail="invalid internal bearer")


def build_router(session_manager: SessionManager, *, http_client: httpx.AsyncClient = None) -> APIRouter:
    router = APIRouter()
    client_holder: Dict[str, httpx.AsyncClient] = {}

    def get_client() -> httpx.AsyncClient:
        if http_client is not None:
            return http_client
        if "client" not in client_holder:
            client_holder["client"] = httpx.AsyncClient(timeout=120.0)
        return client_holder["client"]

    async def _call_upstream(access_token: str, responses_request: Dict[str, Any]) -> httpx.Response:
        client = get_client()
        return await client.post(
            CODEX_RESPONSES_URL,
            headers=_codex_headers(access_token),
            json=responses_request,
        )

    async def _post_with_refresh(responses_request: Dict[str, Any]) -> httpx.Response:
        access_token = await session_manager.ensure_fresh()
        response = await _call_upstream(access_token, responses_request)
        if response.status_code == 401:
            async def retry_call(fresh_token: str) -> httpx.Response:
                return await _call_upstream(fresh_token, responses_request)

            response = await session_manager.handle_401_and_retry(retry_call)
        return response

    @router.get("/v1/models")
    async def list_models(request: Request) -> JSONResponse:
        _check_internal_bearer(request)
        return JSONResponse(
            {
                "object": "list",
                "data": [
                    {
                        "id": CODEX_CLOUD_MODEL,
                        "object": "model",
                        "owned_by": "codex-shim",
                    }
                ],
            }
        )

    @router.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        _check_internal_bearer(request)
        body = await request.json()
        messages = body.get("messages", [])
        tools = body.get("tools")
        stream = bool(body.get("stream", False))
        model = body.get("model") or CODEX_CLOUD_MODEL

        responses_request = build_responses_request(
            model=CODEX_CLOUD_MODEL, messages=messages, tools=tools, stream=stream
        )

        if not stream:
            upstream_response = await _post_with_refresh(responses_request)
            if upstream_response.status_code != 200:
                return JSONResponse(
                    status_code=upstream_response.status_code,
                    content={
                        "error": {
                            "message": f"Upstream returned {upstream_response.status_code}",
                            "type": "upstream_error",
                        }
                    },
                )
            final = _to_namespace(upstream_response.json())
            try:
                completion = assemble_chat_completion(final, model=model)
            except UpstreamResponseError as exc:
                return JSONResponse(status_code=exc.status_code, content=exc.to_openai_error_body())
            return JSONResponse(completion)

        # Streaming path: request SSE from upstream, translate event-by-event.
        access_token = await session_manager.ensure_fresh()
        client = get_client()

        async def event_generator() -> AsyncIterator[str]:
            try:
                async with client.stream(
                    "POST",
                    CODEX_RESPONSES_URL,
                    headers=_codex_headers(access_token),
                    json=dict(responses_request, stream=True),
                ) as upstream:
                    if upstream.status_code == 401:
                        # Reactive refresh: cannot resume a partially-read SSE
                        # response, so restart the stream once with a fresh
                        # token — matches D14's "one refresh, one retry".
                        async def retry_open(fresh_token: str):
                            return fresh_token

                        fresh_token = await session_manager.handle_401_and_retry(retry_open)
                        async with client.stream(
                            "POST",
                            CODEX_RESPONSES_URL,
                            headers=_codex_headers(fresh_token),
                            json=dict(responses_request, stream=True),
                        ) as retried:
                            async for chunk in _translate_httpx_sse(retried, model=model):
                                yield chunk
                        return
                    async for chunk in _translate_httpx_sse(upstream, model=model):
                        yield chunk
            except UpstreamResponseError as exc:
                import json as _json

                yield f"data: {_json.dumps(exc.to_openai_error_body())}\n\n"
                yield "data: [DONE]\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return router


async def _translate_httpx_sse(response: httpx.Response, *, model: str) -> AsyncIterator[str]:
    """Parse an httpx SSE stream of raw Responses events and forward each one
    through `StreamTranslator` as it arrives — true incremental streaming,
    never buffering the whole upstream response before forwarding anything
    to the client. Raw upstream SSE lines are ``data:`` payloads, parsed
    into attribute-style objects so `codex_translate` can treat them
    uniformly with the SDK's typed streaming events."""
    translator = StreamTranslator(model=model)
    async for event in _iter_sse_events(response):
        for line in translator.feed(event):
            yield line
        if translator.done:
            return
    translator.finalize_if_truncated()


async def _iter_sse_events(response: httpx.Response):
    import json as _json

    buffer = ""
    async for raw_line in response.aiter_lines():
        if raw_line.startswith("data:"):
            payload = raw_line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                parsed = _json.loads(payload)
            except Exception:
                continue
            yield _to_namespace(parsed)


def _to_namespace(obj: Any) -> Any:
    """Recursively convert a JSON-decoded dict/list into SimpleNamespace so
    `codex_translate`'s attribute-based accessors work uniformly whether fed
    real SDK objects or plain JSON from the wire."""
    from types import SimpleNamespace

    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _to_namespace(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_namespace(v) for v in obj]
    return obj
