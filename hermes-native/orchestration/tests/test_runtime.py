from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hermes_intent_orchestration.policy import Decision, RouterPolicy
from hermes_intent_orchestration.runtime import OrchestrationRuntime


ROOT = Path(__file__).resolve().parents[1]


class FakeContext:
    manifest = SimpleNamespace(key="intent-orchestration", name="intent-orchestration")


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = OrchestrationRuntime(FakeContext(), ROOT)
        self.policy = RouterPolicy.from_path(ROOT / "policy.yaml")

    def test_toolsets_are_allowlisted_from_capabilities(self) -> None:
        self.assertEqual(self.runtime._toolsets_for(()), ("context_engine",))
        self.assertEqual(
            self.runtime._toolsets_for(("web_search", "files", "tests")),
            ("web", "terminal"),
        )

    def test_synthetic_response_matches_chat_completion_shape(self) -> None:
        response = self.runtime._synthetic_response("answer", "model", "turn")
        self.assertEqual(response.choices[0].message.content, "answer")
        self.assertEqual(response.choices[0].finish_reason, "stop")
        self.assertIsNone(response.usage)

    def test_semantic_classifier_uses_only_local_request_path(self) -> None:
        payload = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"task_class":"chat","complexity":"low",'
                            '"needs_current_data":false,"needs_tools":[],"privacy":"cloud_allowed",'
                            '"risk":"low","route":"local","confidence":0.9,"reason":"timeless question"}'
                        )
                    }
                }
            ]
        }
        with patch.object(self.runtime, "_classifier_is_local", return_value=True), patch.object(
            self.runtime,
            "_main_model_config",
            return_value={"default": "qwen3.5-9b", "base_url": "http://local/v1"},
        ), patch.object(self.runtime, "_local_json_request", return_value=payload) as request:
            classification, fallback = self.runtime._semantic_classification(
                "Explain gravity", self.policy, {"semantic_classifier": True}
            )
        self.assertFalse(fallback)
        self.assertEqual(classification.task_class, "chat")
        request.assert_called_once()
        self.assertEqual(request.call_args.args[1]["chat_template_kwargs"], {"enable_thinking": False})

    def test_semantic_classifier_failure_is_bounded_fallback(self) -> None:
        with patch.object(self.runtime, "_classifier_is_local", return_value=True), patch.object(
            self.runtime, "_main_model_config", return_value={"base_url": "http://local/v1"}
        ), patch.object(self.runtime, "_local_json_request", side_effect=TimeoutError):
            classification, fallback = self.runtime._semantic_classification(
                "Explain gravity", self.policy, {"semantic_classifier": True}
            )
        self.assertIsNone(classification)
        self.assertTrue(fallback)

    def test_shadow_decision_calls_parent_once(self) -> None:
        decision = self.policy.decide("Use terra-medium for this", mode="shadow")
        metadata = {"mode": "shadow", "turn_id": "turn"}
        self.runtime._pending["turn"] = ("prompt", decision, metadata, time.monotonic())
        calls = []

        def next_call(request):
            calls.append(request)
            return "parent"

        with patch.object(self.runtime, "_audit"):
            result = self.runtime.llm_execution(
                request={"messages": []},
                next_call=next_call,
                api_mode="chat_completions",
                api_call_count=1,
                turn_id="turn",
            )
        self.assertEqual(result, "parent")
        self.assertEqual(calls, [{"messages": []}])
        self.assertNotIn("turn", self.runtime._pending)

    def test_explicit_worker_result_short_circuits_parent(self) -> None:
        decision = self.policy.decide("Use terra-medium for this", mode="explicit")
        metadata = {"mode": "explicit", "turn_id": "turn"}
        self.runtime._pending["turn"] = ("prompt", decision, metadata, time.monotonic())
        with patch.object(self.runtime, "_config", return_value={}), patch.object(
            self.runtime, "_run_worker", return_value="worker answer"
        ), patch.object(self.runtime, "_audit"):
            response = self.runtime.llm_execution(
                request={},
                next_call=lambda request: self.fail("parent must not run"),
                api_mode="chat_completions",
                api_call_count=1,
                turn_id="turn",
                model="local",
            )
        self.assertIn("Ruta: terra-medium", response.choices[0].message.content)
        self.assertIn("worker answer", response.choices[0].message.content)

    def test_local_only_uses_pinned_local_completion(self) -> None:
        decision = self.policy.decide("Solo local: analyze my private key", mode="explicit")
        metadata = {"mode": "explicit", "turn_id": "turn"}
        created = time.monotonic()
        self.runtime._pending["turn"] = ("prompt", decision, metadata, created)
        self.runtime._turn_state["turn"] = (decision, metadata, created)
        expected = self.runtime._synthetic_response("local answer", "local", "local")
        with patch.object(self.runtime, "_config", return_value={}), patch.object(
            self.runtime, "_local_completion", return_value=expected
        ) as local, patch.object(self.runtime, "_audit"):
            response = self.runtime.llm_execution(
                request={},
                next_call=lambda request: self.fail("cloud parent must not run"),
                api_mode="chat_completions",
                api_call_count=1,
                turn_id="turn",
                base_url="https://cloud.example",
            )
        self.assertIs(response, expected)
        local.assert_called_once()

    def test_local_only_failure_never_calls_parent(self) -> None:
        decision = self.policy.decide("Solo local: analyze my private key", mode="shadow")
        metadata = {"mode": "shadow", "turn_id": "turn"}
        self.runtime._turn_state["turn"] = (decision, metadata, time.monotonic())
        with patch.object(self.runtime, "_config", return_value={}), patch.object(
            self.runtime, "_local_completion", side_effect=TimeoutError
        ), patch.object(self.runtime, "_audit"):
            response = self.runtime.llm_execution(
                request={},
                next_call=lambda request: self.fail("parent must not run"),
                api_mode="chat_completions",
                api_call_count=2,
                turn_id="turn",
            )
        self.assertIn("No se contacto ningun proveedor cloud", response.choices[0].message.content)

    def test_finish_turn_clears_pending_and_privacy_state(self) -> None:
        decision = self.policy.decide("hello", mode="shadow")
        metadata = {"turn_id": "turn"}
        created = time.monotonic()
        self.runtime._pending["turn"] = ("hello", decision, metadata, created)
        self.runtime._turn_state["turn"] = (decision, metadata, created)
        self.runtime.finish_turn(turn_id="turn")
        self.assertNotIn("turn", self.runtime._pending)
        self.assertNotIn("turn", self.runtime._turn_state)

    def test_explicit_route_blocks_when_classifier_is_disabled(self) -> None:
        config = {
            "mode": "explicit",
            "semantic_classifier": False,
            "require_classifier_for_explicit": True,
            "platforms": ["cli"],
        }
        with patch.object(self.runtime, "_config", return_value=config), patch.object(self.runtime, "_audit"):
            self.runtime.pre_llm_call(
                turn_id="turn",
                session_id="session",
                user_message="Use terra-medium for this",
                platform="cli",
            )
        _text, decision, _metadata, _created = self.runtime._pending["turn"]
        self.assertEqual(decision.rule, "explicit_classifier_unavailable")
        self.assertFalse(decision.should_delegate)

    def test_local_only_filters_external_tools(self) -> None:
        request = {
            "messages": [{"role": "user", "content": "private"}],
            "tools": [
                {"type": "function", "function": {"name": "read_file", "parameters": {}}},
                {"type": "function", "function": {"name": "web_search", "parameters": {}}},
                {"type": "function", "function": {"name": "terminal", "parameters": {}}},
            ],
        }
        response = {"choices": [{"message": {"content": "ok", "tool_calls": None}, "finish_reason": "stop"}]}
        with patch.object(
            self.runtime,
            "_main_model_config",
            return_value={"default": "qwen", "base_url": "http://local/v1"},
        ), patch.object(self.runtime, "_local_json_request", return_value=response) as local:
            self.runtime._local_completion(request, {"local_base_urls": ["http://local/v1"]})
        body = local.call_args.args[1]
        self.assertEqual([tool["function"]["name"] for tool in body["tools"]], ["read_file"])

    def test_stale_state_is_pruned_and_session_cleanup_is_scoped(self) -> None:
        decision = self.policy.decide("hello", mode="shadow")
        stale = time.monotonic() - 601
        fresh = time.monotonic()
        self.runtime._pending["stale"] = ("secret", decision, {"session_id": "old"}, stale)
        self.runtime._turn_state["stale"] = (decision, {"session_id": "old"}, stale)
        self.runtime._pending["fresh"] = ("", decision, {"session_id": "fresh"}, fresh)
        self.runtime._turn_state["fresh"] = (decision, {"session_id": "fresh"}, fresh)
        with self.runtime._lock:
            self.runtime._prune_state(time.monotonic())
        self.assertNotIn("stale", self.runtime._pending)
        self.assertIn("stale", self.runtime._turn_state)
        self.runtime.finish_session(session_id="old")
        self.assertNotIn("stale", self.runtime._turn_state)
        self.runtime.finish_session(session_id="fresh")
        self.assertNotIn("fresh", self.runtime._pending)

    def test_concurrent_turn_cannot_prune_active_privacy_state(self) -> None:
        decision = self.policy.decide("Solo local: private data", mode="shadow")
        self.runtime._turn_state["private"] = (
            decision,
            {"turn_id": "private", "session_id": "private-session"},
            time.monotonic() - 601,
        )
        config = {"mode": "shadow", "semantic_classifier": False, "platforms": ["cli"]}
        with patch.object(self.runtime, "_config", return_value=config), patch.object(self.runtime, "_audit"):
            self.runtime.pre_llm_call(
                turn_id="other", session_id="other-session", user_message="hello", platform="cli"
            )
        self.assertIn("private", self.runtime._turn_state)

    def test_active_turn_refreshes_ttl_before_pruning(self) -> None:
        decision = self.policy.decide("hello", mode="shadow")
        metadata = {"turn_id": "turn"}
        self.runtime._turn_state["turn"] = (decision, metadata, time.monotonic() - 601)
        with patch.object(self.runtime, "_audit"):
            result = self.runtime.llm_execution(
                request={},
                next_call=lambda request: "parent",
                api_mode="chat_completions",
                api_call_count=2,
                turn_id="turn",
            )
        self.assertEqual(result, "parent")
        self.assertIn("turn", self.runtime._turn_state)

    def test_state_overload_forces_unknown_turn_local(self) -> None:
        config = {"mode": "shadow", "semantic_classifier": False, "platforms": ["cli"]}
        with patch("hermes_intent_orchestration.runtime._MAX_TURN_STATES", 1), patch.object(
            self.runtime, "_config", return_value=config
        ), patch.object(self.runtime, "_audit"):
            self.runtime.pre_llm_call(
                turn_id="first", session_id="s", user_message="hello", platform="cli"
            )
            self.runtime.pre_llm_call(
                turn_id="overflow", session_id="s", user_message="hello again", platform="cli"
            )
        expected = self.runtime._synthetic_response("local", "model", "local")
        with patch.object(self.runtime, "_config", return_value={}), patch.object(
            self.runtime, "_local_completion", return_value=expected
        ) as local:
            result = self.runtime.llm_execution(
                request={},
                next_call=lambda request: self.fail("overload must not fail open"),
                api_mode="chat_completions",
                api_call_count=1,
                turn_id="overflow",
            )
        self.assertIs(result, expected)
        local.assert_called_once()

    def test_terminal_worker_requires_explicit_sandbox_opt_in(self) -> None:
        decision = self.policy.decide("Use terra-medium to implement this feature", mode="explicit")
        with self.assertRaises(PermissionError):
            self.runtime._run_worker("implement this feature", decision, {"allow_terminal_workers": False})

    def test_worker_uses_minimal_environment_and_ignores_rules(self) -> None:
        decision = self.policy.decide("Use terra-medium to answer this", mode="explicit")
        captured = {}

        class Process:
            pid = 123
            returncode = 0

            def communicate(self, timeout=None):
                return "worker result", ""

            def poll(self):
                return 0

        def popen(command, **kwargs):
            captured["command"] = command
            captured["env"] = kwargs["env"]
            return Process()

        with patch.dict(os.environ, {"CUSTOM_APPLICATION_SECRET": "must-not-pass"}), patch(
            "hermes_intent_orchestration.runtime.shutil.which", return_value="/usr/bin/hermes"
        ), patch("hermes_intent_orchestration.runtime.subprocess.Popen", side_effect=popen):
            result = self.runtime._run_worker(
                "answer this", decision, {"worker_cwd": "/tmp", "worker_timeout_seconds": 10}
            )
        self.assertEqual(result, "worker result")
        self.assertNotIn("CUSTOM_APPLICATION_SECRET", captured["env"])
        self.assertIn("--ignore-rules", captured["command"])
        self.assertEqual(
            captured["command"][captured["command"].index("--toolsets") + 1],
            "context_engine",
        )

    def test_packaged_policy_assets_work_without_source_root(self) -> None:
        self.runtime.root = Path("/path/that/does/not/exist")
        policy = self.runtime._get_policy({})
        self.assertEqual(policy.decide("Use terra-medium for this", mode="explicit").final_route, "terra-medium")

    def test_audit_schema_never_stores_prompt_content(self) -> None:
        decision = self.policy.decide("Use terra-medium for super-secret-content", mode="explicit")
        with tempfile.TemporaryDirectory() as directory, patch.object(
            self.runtime,
            "_config",
            return_value={"audit_enabled": True, "audit_db": str(Path(directory) / "events.sqlite3")},
        ):
            self.runtime._audit(
                "classified",
                decision,
                {"session_id": "s", "task_id": "t", "turn_id": "u", "platform": "cli", "mode": "explicit"},
            )
            content = (Path(directory) / "events.sqlite3").read_bytes()
        self.assertNotIn(b"super-secret-content", content)


if __name__ == "__main__":
    unittest.main()
