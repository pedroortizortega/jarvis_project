import json
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "hermes-native" / "orchestration" / "src")
)

from memory_router.backends.engram import EngramBackend
from memory_router.contracts import BackendUnavailableError, HealthStatus, SearchRequest, StoreRequest


class FakeStdinStdout:
    def __init__(self, response_line):
        self.written = []
        self._response_line = response_line
        self.flushed = False

    def write(self, data):
        self.written.append(data)

    def flush(self):
        self.flushed = True

    def readline(self):
        return self._response_line


class FakeProcess:
    def __init__(self, response_payload):
        response_line = json.dumps(response_payload) + "\n"
        self.stdin = FakeStdinStdout(response_line)
        self.stdout = self.stdin

    def terminate(self):
        pass


class EngramAdapterSecurityTests(unittest.TestCase):
    def test_spawn_uses_fixed_argv_never_from_caller_input(self):
        captured = {}

        def fake_spawn(argv, env):
            captured["argv"] = argv
            captured["env"] = env
            return FakeProcess({"id": 1, "result": {"id": "note-1"}})

        backend = EngramBackend(
            spawn=fake_spawn, engram_server="http://x", engram_token="tok"
        )
        malicious = StoreRequest(
            namespace="/global",
            role="jarvis",
            content="; rm -rf / #",
            metadata={"argv": ["evil"], "PATH": "/evil"},
        )
        backend.store(malicious)

        self.assertEqual(["engram", "mcp", "--tools=agent"], captured["argv"])

    def test_env_contains_only_fixed_router_owned_keys(self):
        captured = {}

        def fake_spawn(argv, env):
            captured["env"] = env
            return FakeProcess({"id": 1, "result": {"id": "note-1"}})

        backend = EngramBackend(
            spawn=fake_spawn, engram_server="http://x", engram_token="secret-tok"
        )
        malicious = StoreRequest(
            namespace="/global",
            role="jarvis",
            content="hello",
            metadata={"ENGRAM_CLOUD_TOKEN": "attacker-token", "EXTRA": "leak"},
        )
        backend.store(malicious)

        self.assertEqual(
            {"PATH", "ENGRAM_CLOUD_SERVER", "ENGRAM_CLOUD_TOKEN", "ENGRAM_CLOUD_AUTOSYNC"},
            set(captured["env"].keys()),
        )
        self.assertEqual("secret-tok", captured["env"]["ENGRAM_CLOUD_TOKEN"])
        self.assertNotIn("attacker-token", captured["env"].values())

    def test_no_shell_is_ever_requested(self):
        # The default spawn must never use shell=True; verified structurally
        # by asserting the adapter never forwards a shell string, only argv.
        import inspect

        from memory_router.backends import engram as engram_module

        source = inspect.getsource(engram_module)
        self.assertNotIn("shell=True", source)


class EngramAdapterDegradationTests(unittest.TestCase):
    def test_health_is_down_when_subprocess_fails_to_spawn(self):
        def failing_spawn(argv, env):
            raise OSError("engram binary not found")

        backend = EngramBackend(spawn=failing_spawn)
        health = backend.health()
        self.assertEqual(HealthStatus.DOWN, health.status)
        self.assertIn("engram binary not found", health.reason)

    def test_store_raises_backend_unavailable_on_spawn_failure(self):
        def failing_spawn(argv, env):
            raise OSError("no such file")

        backend = EngramBackend(spawn=failing_spawn)
        with self.assertRaises(BackendUnavailableError):
            backend.store(StoreRequest(namespace="/global", role="jarvis", content="x"))

    def test_store_raises_backend_unavailable_when_pipe_breaks(self):
        class DyingProcess:
            def __init__(self):
                self.stdin = self
                self.stdout = self

            def write(self, data):
                raise BrokenPipeError("subprocess died")

            def flush(self):
                pass

            def readline(self):
                return ""

            def terminate(self):
                pass

        backend = EngramBackend(spawn=lambda argv, env: DyingProcess())
        with self.assertRaises(BackendUnavailableError):
            backend.store(StoreRequest(namespace="/global", role="jarvis", content="x"))

    def test_search_raises_backend_unavailable_on_empty_response(self):
        class SilentProcess:
            def __init__(self):
                self.stdin = self
                self.stdout = self

            def write(self, data):
                pass

            def flush(self):
                pass

            def readline(self):
                return ""

            def terminate(self):
                pass

        backend = EngramBackend(spawn=lambda argv, env: SilentProcess())
        with self.assertRaises(BackendUnavailableError):
            backend.search(SearchRequest(namespace="/global", role="jarvis", query="q"))


class EngramAdapterBehaviorTests(unittest.TestCase):
    def test_store_returns_committed_result_on_success(self):
        backend = EngramBackend(
            spawn=lambda argv, env: FakeProcess(
                {"id": 1, "result": {"id": "note-42"}}
            )
        )
        result = backend.store(
            StoreRequest(namespace="/projects/lector-ine", role="coder", content="hi")
        )
        self.assertEqual("committed", result.status)
        self.assertEqual("engram", result.backend)
        self.assertEqual("note-42", result.id)

    def test_search_returns_hits_from_results(self):
        backend = EngramBackend(
            spawn=lambda argv, env: FakeProcess(
                {"id": 1, "result": {"results": [{"content": "found", "score": 0.9}]}}
            )
        )
        result = backend.search(
            SearchRequest(namespace="/global", role="jarvis", query="deploy")
        )
        self.assertEqual(1, len(result.hits))
        self.assertEqual("found", result.hits[0].content)

    def test_capabilities_declare_store_and_search_but_not_reflect(self):
        backend = EngramBackend(spawn=lambda argv, env: FakeProcess({}))
        capabilities = backend.capabilities()
        self.assertEqual({"store", "search"}, set(capabilities.verbs))
        self.assertNotIn("reflect", capabilities.verbs)


if __name__ == "__main__":
    unittest.main()
