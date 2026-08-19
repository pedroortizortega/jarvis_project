"""RED tests 2.9, 2.11 (Phase 2, D15/D15a): streaming response translation.
Target: app.codex_translate.stream_chat_completion_chunks (hand-rolled,
task 3.2c)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest


def _sse_events(chunks):
    for raw in chunks:
        assert raw.startswith("data: ")
        payload = raw[len("data: "):].strip()
        if payload == "[DONE]":
            yield "[DONE]"
            continue
        yield json.loads(payload)


def test_streaming_translation_chunk_shape():
    from app.codex_translate import stream_chat_completion_chunks

    events = [
        SimpleNamespace(type="response.output_text.delta", delta="Hello"),
        SimpleNamespace(type="response.output_text.delta", delta=" world"),
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="message",
                role="assistant",
                content=[SimpleNamespace(type="output_text", text="Hello world")],
            ),
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                status="completed",
                usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
                output=[],
            ),
        ),
    ]

    raw_lines = list(stream_chat_completion_chunks(events, model="gpt-5-codex"))
    assert raw_lines[-1].strip() == "data: [DONE]"

    parsed = list(_sse_events(raw_lines))
    assert parsed[-1] == "[DONE]"

    body_frames = parsed[:-1]
    assert body_frames, "expected at least one chat.completion.chunk frame"
    for frame in body_frames:
        assert frame["object"] == "chat.completion.chunk"
        assert "id" in frame
        assert "created" in frame
        assert "choices" in frame
        assert "delta" in frame["choices"][0]

    delta_texts = [
        f["choices"][0]["delta"].get("content", "")
        for f in body_frames
        if f["choices"][0].get("delta", {}).get("content")
    ]
    assert "".join(delta_texts) == "Hello world"

    final_frame = body_frames[-1]
    assert final_frame["choices"][0]["finish_reason"] in {"stop", "tool_calls"}


def test_streaming_translation_function_call_chunk():
    from app.codex_translate import stream_chat_completion_chunks

    events = [
        SimpleNamespace(
            type="response.output_item.done",
            item=SimpleNamespace(
                type="function_call",
                call_id="call_1",
                name="get_weather",
                arguments='{"city": "SF"}',
            ),
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                status="completed",
                usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
                output=[],
            ),
        ),
    ]

    raw_lines = list(stream_chat_completion_chunks(events, model="gpt-5-codex"))
    parsed = list(_sse_events(raw_lines))
    body_frames = parsed[:-1]

    tool_call_frames = [
        f for f in body_frames if f["choices"][0].get("delta", {}).get("tool_calls")
    ]
    assert tool_call_frames, "expected a tool_calls delta frame"
    tool_call = tool_call_frames[0]["choices"][0]["delta"]["tool_calls"][0]
    assert tool_call["function"]["name"] == "get_weather"

    final_frame = body_frames[-1]
    assert final_frame["choices"][0]["finish_reason"] == "tool_calls"


def test_upstream_error_maps_to_openai_error_shape_streaming():
    from app.codex_translate import stream_chat_completion_chunks, UpstreamResponseError

    events = [
        SimpleNamespace(type="error", code="rate_limit_exceeded", message="Slow down"),
    ]

    with pytest.raises(UpstreamResponseError) as excinfo:
        list(stream_chat_completion_chunks(events, model="gpt-5-codex"))

    body = excinfo.value.to_openai_error_body()
    assert "error" in body
    assert body["error"]["message"]


def test_upstream_failed_terminal_maps_to_openai_error_shape_streaming():
    from app.codex_translate import stream_chat_completion_chunks, UpstreamResponseError

    events = [
        SimpleNamespace(
            type="response.failed",
            response=SimpleNamespace(
                status="failed",
                error=SimpleNamespace(code="internal_error", message="boom"),
            ),
        ),
    ]

    with pytest.raises(UpstreamResponseError) as excinfo:
        list(stream_chat_completion_chunks(events, model="gpt-5-codex"))

    body = excinfo.value.to_openai_error_body()
    assert body["error"]["code"] == "internal_error"


def test_upstream_incomplete_terminal_maps_to_openai_error_shape_streaming():
    from app.codex_translate import stream_chat_completion_chunks, UpstreamResponseError

    events = [
        SimpleNamespace(
            type="response.incomplete",
            response=SimpleNamespace(
                status="incomplete",
                incomplete_details=SimpleNamespace(reason="content_filter"),
            ),
        ),
    ]

    with pytest.raises(UpstreamResponseError):
        list(stream_chat_completion_chunks(events, model="gpt-5-codex"))
