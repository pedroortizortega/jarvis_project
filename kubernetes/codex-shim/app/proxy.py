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
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.codex_auth import CODEX_RATE_LIMITED_CODE, AuthError
from app.codex_translate import (
    StreamTranslator,
    UpstreamResponseError,
    _error_from_event,
    assemble_chat_completion,
    build_responses_request,
)
from app.session import SessionManager
from app.store import SecretNotFound

logger = logging.getLogger(__name__)

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
CODEX_CLOUD_MODEL = os.environ.get("CODEX_CLOUD_MODEL", "gpt-5.6-sol")
INTERNAL_KEY_ENV = "CODEX_SHIM_INTERNAL_KEY"


def _session_error_body(exc: Exception) -> tuple[int, Dict[str, Any]]:
    """Map a refresh-time failure to a structured (status, body) pair instead
    of letting it surface as an opaque 500. Found by /code-review (Amendment
    5): neither the non-streaming nor the streaming call site caught
    `AuthError`/`SecretNotFound` from `ensure_fresh()`/`handle_401_and_retry()`
    — a client that hit either got a generic unhandled-exception 500 with no
    indication of whether re-login is needed or it's a transient failure."""
    if isinstance(exc, SecretNotFound):
        return 503, {"error": {"message": "not_configured", "type": "session_error", "state": "not_configured"}}
    if isinstance(exc, AuthError):
        if exc.code == CODEX_RATE_LIMITED_CODE:
            return 429, {"error": {"message": str(exc), "type": "session_error", "state": "rate_limited"}}
        if exc.relogin_required:
            return 401, {"error": {"message": str(exc), "type": "session_error", "state": "expired_needs_relogin"}}
        return 503, {"error": {"message": str(exc), "type": "session_error", "state": "refresh_failed"}}
    raise TypeError(f"_session_error_body called with non-session exception: {exc!r}")


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

    @router.post("/v1/responses")
    async def responses_passthrough(request: Request):
        """Found live (Amendment 5): LiteLLM bridges Chat Completions calls
        carrying both `reasoning_effort` and `tools` for gpt-5.4+ models
        (`responses_api_bridge_check` in litellm/main.py) to its OWN
        Responses-API client, POSTing the ALREADY Responses-API-shaped body
        straight to `{api_base}/responses` — bypassing `/v1/chat/completions`
        entirely. Hermes sends both params on every agentic turn, so this is
        not an edge case, it is the common path. Unlike that endpoint, no
        translation is needed here: the caller (LiteLLM) already speaks the
        same shape our real upstream speaks, so this is a near-direct proxy
        — swap auth, forward the body, forward the response/stream back
        byte-for-byte."""
        _check_internal_bearer(request)
        body = await request.json()
        stream = bool(body.get("stream", False))

        # Same constraint as chat_completions: upstream rejects `stream`
        # omitted/false for this account. Always stream upstream; buffer
        # into one JSON response below if the caller didn't ask for
        # streaming. Also confirmed live: upstream rejects `store` anything
        # but `false` (`{"detail":"Store must be set to false"}`) — LiteLLM's
        # Responses-API bridge does not know about either account-specific
        # constraint, so both must be forced here regardless of what the
        # caller sent.
        upstream_body = dict(body)
        upstream_body["stream"] = True
        upstream_body["store"] = False
        # Confirmed live + matches Hermes's own known-good Codex client
        # (auxiliary_client.py: "the Codex endpoint ... does NOT support
        # max_output_tokens or temperature — omit to avoid 400 errors").
        # LiteLLM's Responses-API bridge translates the caller's
        # `max_tokens` into `max_output_tokens` with no knowledge of this
        # account-specific restriction.
        upstream_body.pop("max_output_tokens", None)
        upstream_body.pop("temperature", None)

        try:
            access_token = await session_manager.ensure_fresh()
        except (AuthError, SecretNotFound) as exc:
            status_code, err_body = _session_error_body(exc)
            return JSONResponse(status_code=status_code, content=err_body)
        client = get_client()

        if not stream:
            final_response: Optional[Dict[str, Any]] = None

            async def _consume(token: str) -> tuple:
                nonlocal final_response
                # Same fix as chat_completions' non-streaming path (D15
                # Amendment 4): the terminal `response.completed` event's
                # own `response.output` is genuinely empty for this
                # account — the real items only ever arrive earlier via
                # `response.output_item.done`. LiteLLM's Responses-API
                # client itself rejects an `output: []` body
                # ("Unknown items in responses API response: []"), so this
                # passthrough is not fully byte-for-byte for the
                # non-streaming case: the collected items must be spliced
                # back in.
                collected_items: list = []
                async with client.stream(
                    "POST", CODEX_RESPONSES_URL, headers=_codex_headers(token), json=upstream_body
                ) as upstream:
                    if upstream.status_code != 200:
                        return upstream.status_code, await upstream.aread()
                    async for event in _iter_sse_events(upstream):
                        error = _error_from_event(event)
                        if error is not None:
                            raise error
                        event_type = getattr(event, "type", None)
                        if event_type == "response.output_item.done":
                            item = getattr(event, "item", None)
                            if item is not None:
                                collected_items.append(item)
                        elif event_type in ("response.completed", "response.incomplete", "response.failed"):
                            response_ns = getattr(event, "response", event)
                            final_response = _namespace_to_jsonable(response_ns)
                            output = getattr(response_ns, "output", None)
                            if not (isinstance(output, list) and output):
                                final_response["output"] = _namespace_to_jsonable(collected_items)
                    return 200, None

            try:
                status_code, error_body = await _consume(access_token)
                if status_code == 401:
                    async def retry(fresh_token: str):
                        return await _consume(fresh_token)

                    try:
                        status_code, error_body = await session_manager.handle_401_and_retry(retry)
                    except AuthError as exc:
                        status_code, err_body = _session_error_body(exc)
                        return JSONResponse(status_code=status_code, content=err_body)
            except UpstreamResponseError as exc:
                return JSONResponse(status_code=exc.status_code, content=exc.to_openai_error_body())

            if status_code != 200:
                detail = error_body.decode(errors="replace")[:500] if error_body else ""
                logger.warning("upstream non-200 on /v1/responses non-streaming call: %s", status_code)
                return JSONResponse(
                    status_code=status_code,
                    content={
                        "error": {
                            "message": f"Upstream returned {status_code}",
                            "type": "upstream_error",
                            "detail": detail,
                        }
                    },
                )
            if final_response is None:
                return JSONResponse(
                    status_code=502,
                    content={"error": {"message": "Upstream stream ended without a terminal event", "type": "upstream_error"}},
                )
            return JSONResponse(final_response)

        # Streaming: byte-for-byte passthrough — no translation, LiteLLM's
        # own Responses-API SSE parser reads this directly.
        async def event_generator() -> AsyncIterator[bytes]:
            try:
                async with client.stream(
                    "POST", CODEX_RESPONSES_URL, headers=_codex_headers(access_token), json=upstream_body
                ) as upstream:
                    if upstream.status_code == 401:
                        async def retry_open(fresh_token: str):
                            return fresh_token

                        try:
                            fresh_token = await session_manager.handle_401_and_retry(retry_open)
                        except AuthError as exc:
                            import json as _json

                            _status, err_body = _session_error_body(exc)
                            yield f"data: {_json.dumps(err_body)}\n\n".encode()
                            return
                        async with client.stream(
                            "POST", CODEX_RESPONSES_URL, headers=_codex_headers(fresh_token), json=upstream_body
                        ) as retried:
                            async for chunk in retried.aiter_bytes():
                                yield chunk
                        return
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
            except Exception:
                logger.exception("codex-shim: /v1/responses streaming passthrough failed")

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @router.post("/v1/chat/completions")
    async def chat_completions(request: Request):
        _check_internal_bearer(request)
        body = await request.json()
        messages = body.get("messages", [])
        tools = body.get("tools")
        stream = bool(body.get("stream", False))
        model = body.get("model") or CODEX_CLOUD_MODEL

        # The upstream Responses API rejects `stream` omitted/false for this
        # account (`"detail":"Stream must be set to true"`, confirmed live
        # against https://chatgpt.com/backend-api/codex — see design.md
        # Amendment 4). Upstream is therefore ALWAYS asked to stream; the
        # caller's own `stream` flag only decides whether we forward chunks
        # as they arrive or buffer them into one assembled response below.
        responses_request = build_responses_request(
            model=CODEX_CLOUD_MODEL, messages=messages, tools=tools, stream=True
        )

        if not stream:
            try:
                access_token = await session_manager.ensure_fresh()
            except (AuthError, SecretNotFound) as exc:
                status_code, body = _session_error_body(exc)
                return JSONResponse(status_code=status_code, content=body)
            client = get_client()
            final_response = None

            async def _consume(token: str) -> tuple:
                nonlocal final_response
                # The real Responses API's terminal `response.completed`
                # event carries an EMPTY `response.output` — the actual
                # message/tool-call items only ever arrive earlier, one at a
                # time, via `response.output_item.done` events during the
                # stream (confirmed live against
                # https://chatgpt.com/backend-api/codex — see design.md
                # Amendment 4). They must be accumulated as they arrive and
                # substituted in for the terminal event's empty `output`.
                collected_items: list = []
                async with client.stream(
                    "POST",
                    CODEX_RESPONSES_URL,
                    headers=_codex_headers(token),
                    json=responses_request,
                ) as upstream:
                    if upstream.status_code != 200:
                        body_bytes = await upstream.aread()
                        return upstream.status_code, body_bytes
                    async for event in _iter_sse_events(upstream):
                        # Found by /code-review (Amendment 5): a standalone
                        # `type: "error"` event with no terminal
                        # response.completed/failed event following it was
                        # silently ignored here — final_response stayed
                        # None and the caller got a generic 502 instead of
                        # the real error the streaming path already
                        # surfaces via the same `_error_from_event` check.
                        error = _error_from_event(event)
                        if error is not None:
                            raise error
                        event_type = getattr(event, "type", None)
                        if event_type == "response.output_item.done":
                            item = getattr(event, "item", None)
                            if item is not None:
                                collected_items.append(item)
                        elif event_type in ("response.completed", "response.incomplete", "response.failed"):
                            terminal_response = getattr(event, "response", event)
                            output = getattr(terminal_response, "output", None)
                            final_response = SimpleNamespace(
                                output=collected_items or (output if isinstance(output, list) else []),
                                usage=getattr(terminal_response, "usage", None),
                                status=getattr(terminal_response, "status", None),
                                error=getattr(terminal_response, "error", None),
                                incomplete_details=getattr(terminal_response, "incomplete_details", None),
                            )
                    return 200, None

            try:
                status_code, error_body = await _consume(access_token)
                if status_code == 401:
                    async def retry(fresh_token: str):
                        return await _consume(fresh_token)

                    try:
                        status_code, error_body = await session_manager.handle_401_and_retry(retry)
                    except AuthError as exc:
                        status_code, body = _session_error_body(exc)
                        return JSONResponse(status_code=status_code, content=body)
            except UpstreamResponseError as exc:
                return JSONResponse(status_code=exc.status_code, content=exc.to_openai_error_body())
            if status_code != 200:
                detail = error_body.decode(errors="replace")[:500] if error_body else ""
                logger.warning("upstream non-200 on non-streaming call: %s", status_code)
                return JSONResponse(
                    status_code=status_code,
                    content={
                        "error": {
                            "message": f"Upstream returned {status_code}",
                            "type": "upstream_error",
                            "detail": detail,
                        }
                    },
                )
            if final_response is None:
                return JSONResponse(
                    status_code=502,
                    content={"error": {"message": "Upstream stream ended without a terminal event", "type": "upstream_error"}},
                )
            try:
                completion = assemble_chat_completion(final_response, model=model)
            except UpstreamResponseError as exc:
                return JSONResponse(status_code=exc.status_code, content=exc.to_openai_error_body())
            return JSONResponse(completion)

        # Streaming path: request SSE from upstream, translate event-by-event.
        try:
            access_token = await session_manager.ensure_fresh()
        except (AuthError, SecretNotFound) as exc:
            status_code, body = _session_error_body(exc)
            return JSONResponse(status_code=status_code, content=body)
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

                        try:
                            fresh_token = await session_manager.handle_401_and_retry(retry_open)
                        except AuthError as exc:
                            import json as _json

                            _status, body = _session_error_body(exc)
                            yield f"data: {_json.dumps(body)}\n\n"
                            yield "data: [DONE]\n\n"
                            return
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


def _namespace_to_jsonable(obj: Any) -> Any:
    """Inverse of `_to_namespace`: recursively convert a SimpleNamespace
    (produced by `_iter_sse_events`) back into a plain JSON-serializable
    dict/list, for `/v1/responses`'s non-streaming path — the terminal
    event's `.response` must be returned to the caller as the raw Responses
    API object, not an attribute-access shim."""
    from types import SimpleNamespace

    if isinstance(obj, SimpleNamespace):
        return {k: _namespace_to_jsonable(v) for k, v in vars(obj).items()}
    if isinstance(obj, list):
        return [_namespace_to_jsonable(v) for v in obj]
    return obj


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
