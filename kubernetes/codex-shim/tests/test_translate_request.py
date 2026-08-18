"""RED tests 2.7-2.8 (Phase 2, D15/D15a): request-side translation.
Target: app.codex_translate._chat_messages_to_responses_input / _responses_tools
(vendored from codex_responses_adapter.py, task 3.2a)."""

from __future__ import annotations


def test_request_translation_messages_to_input_and_instructions():
    from app.codex_translate import build_responses_request

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    request = build_responses_request(model="gpt-5-codex", messages=messages, tools=tools)

    assert request["instructions"] == "You are a helpful assistant."
    roles = [item.get("role") for item in request["input"] if "role" in item]
    assert "system" not in roles
    assert {"user", "assistant"}.issubset(set(roles))
    assert request["tools"][0]["type"] == "function"
    assert request["tools"][0]["name"] == "get_weather"


def test_request_translation_tool_role_becomes_function_call_output():
    from app.codex_translate import build_responses_request

    messages = [
        {"role": "user", "content": "what is the weather?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_abc123",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city": "SF"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_abc123", "content": "72F sunny"},
    ]

    request = build_responses_request(model="gpt-5-codex", messages=messages, tools=None)

    # No role="tool" item must ever reach the Responses payload — the
    # upstream API rejects that shape outright.
    for item in request["input"]:
        assert item.get("role") != "tool"

    function_call_outputs = [
        item for item in request["input"] if item.get("type") == "function_call_output"
    ]
    assert len(function_call_outputs) == 1
    assert function_call_outputs[0]["call_id"] == "call_abc123"
    assert function_call_outputs[0]["output"] == "72F sunny"

    function_calls = [item for item in request["input"] if item.get("type") == "function_call"]
    assert len(function_calls) == 1
    assert function_calls[0]["call_id"] == "call_abc123"
    assert function_calls[0]["name"] == "get_weather"
