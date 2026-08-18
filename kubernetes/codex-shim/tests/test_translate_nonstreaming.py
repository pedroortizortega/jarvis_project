"""RED test 2.10 (Phase 2, D15/D15a): non-streaming response translation.
Target: app.codex_translate.assemble_chat_completion (mirrors
_CodexCompletionsAdapter's response assembly, task 3.2b)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_non_streaming_translation_single_object():
    from app.codex_translate import assemble_chat_completion

    final_response = SimpleNamespace(
        id="resp_123",
        status="completed",
        output=[
            SimpleNamespace(
                type="message",
                role="assistant",
                status="completed",
                content=[SimpleNamespace(type="output_text", text="Hello world")],
            ),
        ],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15),
    )

    completion = assemble_chat_completion(final_response, model="gpt-5-codex")

    assert completion["object"] == "chat.completion"
    assert completion["choices"][0]["message"]["content"] == "Hello world"
    assert completion["choices"][0]["message"].get("tool_calls") in (None, [])
    assert completion["choices"][0]["finish_reason"] == "stop"
    assert completion["usage"]["prompt_tokens"] == 10
    assert completion["usage"]["completion_tokens"] == 5
    assert completion["usage"]["total_tokens"] == 15


def test_non_streaming_translation_with_tool_calls():
    from app.codex_translate import assemble_chat_completion

    final_response = SimpleNamespace(
        id="resp_456",
        status="completed",
        output=[
            SimpleNamespace(
                type="function_call",
                call_id="call_1",
                name="get_weather",
                arguments='{"city": "SF"}',
            ),
        ],
        usage=SimpleNamespace(input_tokens=8, output_tokens=2, total_tokens=10),
    )

    completion = assemble_chat_completion(final_response, model="gpt-5-codex")

    message = completion["choices"][0]["message"]
    assert message["tool_calls"][0]["function"]["name"] == "get_weather"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"city": "SF"}'
    assert completion["choices"][0]["finish_reason"] == "tool_calls"


def test_upstream_error_maps_to_openai_error_shape_non_streaming():
    from app.codex_translate import assemble_chat_completion, UpstreamResponseError

    final_response = SimpleNamespace(
        id="resp_err",
        status="failed",
        output=[],
        error=SimpleNamespace(code="internal_error", message="boom"),
    )

    with pytest.raises(UpstreamResponseError) as excinfo:
        assemble_chat_completion(final_response, model="gpt-5-codex")

    body = excinfo.value.to_openai_error_body()
    assert body["error"]["code"] == "internal_error"
