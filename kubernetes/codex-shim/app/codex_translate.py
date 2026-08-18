"""Chat Completions <-> Responses API translation (D15/D15a).

The request-side converters (`_chat_messages_to_responses_input`,
`_responses_tools`) are vendored verbatim from the repo's own
`codex_responses_adapter.py` (already-debugged edge handling, notably
`role: "tool"` -> `function_call_output` re-encoding, which the Responses API
otherwise rejects outright). The non-streaming response assembly mirrors
`_CodexCompletionsAdapter`'s logic in `auxiliary_client.py`. The streaming
chunk emitter is hand-rolled — no reusable streaming-out code exists
upstream (see design.md D15a).

Provenance
----------
Source file:    kubernetes/docker/hermes-agent/agent/codex_responses_adapter.py
Source symbols: `_chat_messages_to_responses_input`, `_responses_tools`
                (vendored verbatim below).
Source file:    kubernetes/docker/hermes-agent/agent/auxiliary_client.py
Source symbols: `_CodexCompletionsAdapter.create` (lines ~952-1295) — response
                assembly pattern mirrored (not copied verbatim; adapted to a
                pure function over a completed Responses object instead of
                consuming a live event stream itself).
Source repo state: `kubernetes/docker/hermes-agent/` is excluded from this
                repository's git history (see .gitignore:222) — there is no
                commit hash to cite. Vendored/mirrored as of 2026-08-17.
                codex_responses_adapter.py sha256:
                4608e8217d5917ec739cdec93ece3323ebcd868286c142e38973b26bc6520d3e
                auxiliary_client.py sha256:
                d770dba4f1e5403ad8a7b7dc0d51a168cc2f14a5c21892d62a875663263a5a83
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from typing import Any, Dict, Iterable, Iterator, List, Optional


# ---------------------------------------------------------------------------
# Vendored (verbatim behaviour) from codex_responses_adapter.py
# ---------------------------------------------------------------------------

def _split_responses_tool_id(raw_id: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(raw_id, str):
        return None, None
    value = raw_id.strip()
    if not value:
        return None, None
    if "|" in value:
        call_id, response_item_id = value.split("|", 1)
        call_id = call_id.strip() or None
        response_item_id = response_item_id.strip() or None
        return call_id, response_item_id
    if value.startswith("fc_"):
        return None, value
    return value, None


def _deterministic_call_id(fn_name: str, arguments: str, index: int = 0) -> str:
    seed = f"{fn_name}:{arguments}:{index}"
    digest = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"call_{digest}"


def _responses_tools(tools: Optional[List[Dict[str, Any]]] = None) -> Optional[List[Dict[str, Any]]]:
    """Convert chat-completions tool schemas to Responses function-tool schemas."""
    if not tools:
        return None

    converted: List[Dict[str, Any]] = []
    for item in tools:
        fn = item.get("function", {}) if isinstance(item, dict) else {}
        name = fn.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        converted.append({
            "type": "function",
            "name": name,
            "description": fn.get("description", ""),
            "strict": False,
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return converted or None


def _chat_messages_to_responses_input(
    messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Convert internal chat-style messages to Responses input items.

    Trimmed to the shim's actual needs (no reasoning replay / multimodal /
    cross-issuer machinery — codex-shim proxies plain Chat Completions
    requests from LiteLLM, it never carries Hermes's internal
    `codex_reasoning_items` metadata). The `role: "tool"` -> Responses
    rejection-avoidance path is preserved verbatim, since that is the
    concrete bug this vendoring exists to avoid.
    """
    items: List[Dict[str, Any]] = []

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "system":
            continue

        if role in {"user", "assistant"}:
            content = msg.get("content", "")
            content_text = str(content) if content is not None else ""

            if role == "assistant":
                if content_text.strip():
                    items.append({"role": "assistant", "content": content_text})

                tool_calls = msg.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tc in tool_calls:
                        if not isinstance(tc, dict):
                            continue
                        fn = tc.get("function", {})
                        fn_name = fn.get("name")
                        if not isinstance(fn_name, str) or not fn_name.strip():
                            continue

                        embedded_call_id, embedded_response_item_id = _split_responses_tool_id(
                            tc.get("id")
                        )
                        call_id = tc.get("call_id")
                        if not isinstance(call_id, str) or not call_id.strip():
                            call_id = embedded_call_id
                        if not isinstance(call_id, str) or not call_id.strip():
                            if (
                                isinstance(embedded_response_item_id, str)
                                and embedded_response_item_id.startswith("fc_")
                                and len(embedded_response_item_id) > len("fc_")
                            ):
                                call_id = f"call_{embedded_response_item_id[len('fc_'):]}"
                            else:
                                _raw_args = str(fn.get("arguments", "{}"))
                                call_id = _deterministic_call_id(fn_name, _raw_args, len(items))
                        call_id = call_id.strip()

                        arguments = fn.get("arguments", "{}")
                        if isinstance(arguments, dict):
                            arguments = json.dumps(arguments, ensure_ascii=False)
                        elif not isinstance(arguments, str):
                            arguments = str(arguments)
                        arguments = arguments.strip() or "{}"

                        items.append({
                            "type": "function_call",
                            "call_id": call_id,
                            "name": fn_name,
                            "arguments": arguments,
                        })
                continue

            items.append({"role": role, "content": content_text})
            continue

        if role == "tool":
            raw_tool_call_id = msg.get("tool_call_id")
            call_id, _ = _split_responses_tool_id(raw_tool_call_id)
            if not isinstance(call_id, str) or not call_id.strip():
                if isinstance(raw_tool_call_id, str) and raw_tool_call_id.strip():
                    call_id = raw_tool_call_id.strip()
            if not isinstance(call_id, str) or not call_id.strip():
                continue

            tool_content = msg.get("content")
            output_value = str(tool_content or "")

            items.append({
                "type": "function_call_output",
                "call_id": call_id,
                "output": output_value,
            })

    return items


# ---------------------------------------------------------------------------
# Request-side translation entry point (3.2a)
# ---------------------------------------------------------------------------

DEFAULT_INSTRUCTIONS = "You are a helpful assistant."


def build_responses_request(
    *,
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    stream: bool = False,
) -> Dict[str, Any]:
    """Chat Completions request -> Responses API request (D15)."""
    instructions = DEFAULT_INSTRUCTIONS
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "system":
            content = msg.get("content")
            if isinstance(content, str) and content.strip():
                instructions = content
            break

    input_items = _chat_messages_to_responses_input(messages)
    responses_tools = _responses_tools(tools)

    request: Dict[str, Any] = {
        "model": model,
        "instructions": instructions,
        "input": input_items or [{"role": "user", "content": ""}],
        "store": False,
    }
    if responses_tools:
        request["tools"] = responses_tools
    if stream:
        request["stream"] = True
    return request


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class UpstreamResponseError(RuntimeError):
    """Raised when the Responses API reports an error/failed/incomplete
    terminal state. Carries enough to build an OpenAI-shaped error body
    instead of ever returning a truncated 200 (D15)."""

    def __init__(self, message: str, *, code: Optional[str] = None, status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.status_code = status_code

    def to_openai_error_body(self) -> Dict[str, Any]:
        return {
            "error": {
                "message": str(self),
                "type": "upstream_error",
                "code": self.code,
            }
        }


def _error_from_event(event: Any) -> Optional[UpstreamResponseError]:
    event_type = getattr(event, "type", None)
    if event_type == "error":
        code = getattr(event, "code", None)
        message = getattr(event, "message", None) or "Responses API returned an error event."
        return UpstreamResponseError(str(message), code=code)

    if event_type in {"response.failed", "response.incomplete"}:
        response = getattr(event, "response", None)
        error_obj = getattr(response, "error", None)
        if error_obj is not None:
            code = getattr(error_obj, "code", None)
            message = getattr(error_obj, "message", None) or str(error_obj)
            return UpstreamResponseError(str(message), code=code)
        incomplete_details = getattr(response, "incomplete_details", None)
        reason = getattr(incomplete_details, "reason", None) if incomplete_details else None
        message = f"Responses API terminated as {event_type} (reason={reason})."
        return UpstreamResponseError(message, code=reason or event_type)

    return None


def _error_from_final_response(final_response: Any) -> Optional[UpstreamResponseError]:
    status = getattr(final_response, "status", None)
    if status in {"failed", "cancelled"}:
        error_obj = getattr(final_response, "error", None)
        code = getattr(error_obj, "code", None) if error_obj is not None else None
        message = (
            getattr(error_obj, "message", None)
            if error_obj is not None
            else None
        ) or f"Responses API returned status '{status}'."
        return UpstreamResponseError(str(message), code=code)
    return None


# ---------------------------------------------------------------------------
# Non-streaming response assembly (3.2b) — mirrors _CodexCompletionsAdapter
# ---------------------------------------------------------------------------

def _extract_message_text(item: Any) -> str:
    content = getattr(item, "content", None)
    if not isinstance(content, list):
        return ""
    chunks: List[str] = []
    for part in content:
        ptype = getattr(part, "type", None)
        if ptype not in {"output_text", "text"}:
            continue
        text = getattr(part, "text", None)
        if isinstance(text, str) and text:
            chunks.append(text)
    return "".join(chunks)


def _tool_call_dict(call_id: str, name: str, arguments: str) -> Dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def assemble_chat_completion(final_response: Any, *, model: str) -> Dict[str, Any]:
    """Collapse a completed Responses result into one `chat.completion`
    object (3.2b). Raises `UpstreamResponseError` on a failed/cancelled
    terminal response instead of returning a truncated 200."""
    error = _error_from_final_response(final_response)
    if error is not None:
        raise error

    text_parts: List[str] = []
    tool_calls: List[Dict[str, Any]] = []

    for item in getattr(final_response, "output", None) or []:
        item_type = getattr(item, "type", None)
        if item_type == "message":
            text = _extract_message_text(item)
            if text:
                text_parts.append(text)
        elif item_type == "function_call":
            name = getattr(item, "name", "") or ""
            arguments = getattr(item, "arguments", "{}")
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            call_id = getattr(item, "call_id", None) or _deterministic_call_id(
                name, arguments, len(tool_calls)
            )
            tool_calls.append(_tool_call_dict(call_id, name, arguments))

    content = "".join(text_parts).strip() or None
    finish_reason = "tool_calls" if tool_calls else "stop"

    message: Dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls

    usage_obj = getattr(final_response, "usage", None)
    prompt_tokens = getattr(usage_obj, "input_tokens", 0) or 0 if usage_obj else 0
    completion_tokens = getattr(usage_obj, "output_tokens", 0) or 0 if usage_obj else 0
    total_tokens = (
        getattr(usage_obj, "total_tokens", None) or (prompt_tokens + completion_tokens)
        if usage_obj
        else 0
    )

    return {
        "id": getattr(final_response, "id", None) or f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
    }


# ---------------------------------------------------------------------------
# Streaming response translation (3.2c) — hand-rolled, no upstream reuse
# ---------------------------------------------------------------------------

def _sse(payload: Dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class StreamTranslator:
    """Incremental Responses-event -> `chat.completion.chunk` translator.

    Exposes `feed(event) -> List[str]` so a caller can forward each upstream
    SSE event to the client as soon as it arrives (true streaming, not
    buffered into one blob) — `stream_chat_completion_chunks` below is a
    thin synchronous-iterable convenience wrapper over this class for
    non-streaming callers/tests.
    """

    def __init__(self, *, model: str, chunk_id: Optional[str] = None):
        self.model = model
        self.chunk_id = chunk_id or f"chatcmpl-{uuid.uuid4().hex[:24]}"
        self.created = int(time.time())
        self.saw_tool_call = False
        self.done = False

    def _base_chunk(self, delta: Dict[str, Any], finish_reason: Optional[str] = None) -> Dict[str, Any]:
        choice: Dict[str, Any] = {"index": 0, "delta": delta}
        if finish_reason is not None:
            choice["finish_reason"] = finish_reason
        return {
            "id": self.chunk_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [choice],
        }

    def feed(self, event: Any) -> List[str]:
        """Return zero or more SSE lines for this event. Raises
        `UpstreamResponseError` on an error/failed/incomplete event."""
        if self.done:
            return []

        error = _error_from_event(event)
        if error is not None:
            raise error

        event_type = getattr(event, "type", None)
        out: List[str] = []

        if event_type == "response.output_text.delta":
            delta_text = getattr(event, "delta", "") or ""
            if delta_text:
                out.append(_sse(self._base_chunk({"content": delta_text})))
            return out

        if event_type == "response.output_item.done":
            item = getattr(event, "item", None)
            item_type = getattr(item, "type", None) if item is not None else None
            if item_type == "function_call":
                self.saw_tool_call = True
                name = getattr(item, "name", "") or ""
                arguments = getattr(item, "arguments", "{}")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments, ensure_ascii=False)
                call_id = getattr(item, "call_id", None) or _deterministic_call_id(
                    name, arguments, 0
                )
                tool_call_delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    ]
                }
                out.append(_sse(self._base_chunk(tool_call_delta)))
            # message-type output_item.done carries no new text beyond the
            # already-streamed output_text.delta events — nothing to emit.
            return out

        if event_type == "response.completed":
            response = getattr(event, "response", None)
            usage_obj = getattr(response, "usage", None) if response is not None else None
            usage_dict = None
            if usage_obj is not None:
                prompt_tokens = getattr(usage_obj, "input_tokens", 0) or 0
                completion_tokens = getattr(usage_obj, "output_tokens", 0) or 0
                total_tokens = getattr(usage_obj, "total_tokens", None) or (
                    prompt_tokens + completion_tokens
                )
                usage_dict = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                }
            finish_reason = "tool_calls" if self.saw_tool_call else "stop"
            final_chunk = self._base_chunk({}, finish_reason=finish_reason)
            if usage_dict is not None:
                final_chunk["usage"] = usage_dict
            out.append(_sse(final_chunk))
            out.append("data: [DONE]\n\n")
            self.done = True
            return out

        return out

    def finalize_if_truncated(self) -> None:
        """Call after the event source is exhausted; raises if no terminal
        event was ever observed (protects against a truncated 200)."""
        if not self.done:
            raise UpstreamResponseError(
                "Responses event stream ended without a terminal event.",
                code="stream_truncated",
            )


def stream_chat_completion_chunks(
    events: Iterable[Any], *, model: str, chunk_id: Optional[str] = None
) -> Iterator[str]:
    """Translate a Responses SSE event sequence into
    `chat.completion.chunk` SSE frames + a terminal `data: [DONE]` (D15/D15a).

    Synchronous convenience wrapper over `StreamTranslator` for callers that
    already hold the full event sequence (e.g. unit tests). The async proxy
    path feeds `StreamTranslator` directly, event-by-event, to avoid
    buffering the whole upstream response before forwarding anything.
    """
    translator = StreamTranslator(model=model, chunk_id=chunk_id)
    for event in events:
        for line in translator.feed(event):
            yield line
        if translator.done:
            return
    translator.finalize_if_truncated()
