import json
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "hermes-native" / "memory-router" / "src")
)

from memory_router.backends.engram import EngramBackend
from memory_router.backends.hindsight import HindsightBackend
from memory_router.backends.honcho import HonchoBackend
from memory_router.contracts import (
    BackendUnavailableError,
    HealthStatus,
    MemoryBackend,
    ReflectRequest,
    ReflectiveBackend,
)


class ContractConformanceTests(unittest.TestCase):
    """Baseline: existing adapters' MemoryBackend conformance must remain
    untouched by this change. Run against current code to confirm it
    already passes."""

    def test_engram_still_conforms_to_memory_backend(self):
        backend = EngramBackend(spawn=lambda argv, env: None)
        self.assertIsInstance(backend, MemoryBackend)

    def test_hindsight_still_conforms_to_memory_backend(self):
        backend = HindsightBackend(base_url="http://x")
        self.assertIsInstance(backend, MemoryBackend)


class ReflectiveProtocolIsSeparateTests(unittest.TestCase):
    def test_reflective_backend_is_separate_protocol(self):
        self.assertFalse(hasattr(MemoryBackend, "reflect"))
        self.assertFalse(
            isinstance(HindsightBackend(base_url="http://x"), ReflectiveBackend)
        )


class HonchoAdapterConfigTests(unittest.TestCase):
    def test_zero_arg_construction_succeeds(self):
        backend = HonchoBackend()
        self.assertIsInstance(backend, HonchoBackend)

    def test_defaults_when_no_env_and_no_explicit_args(self):
        import os

        saved = {
            key: os.environ.pop(key, None)
            for key in (
                "HONCHO_BASE_URL",
                "HONCHO_AUTH_MODE",
                "HONCHO_TOKEN",
                "HONCHO_WORKSPACE_ID",
                "HONCHO_TIMEOUT_SECONDS",
            )
        }
        try:
            backend = HonchoBackend()
            self.assertEqual(
                "http://honcho.mcps.svc.cluster.local:8000", backend._base_url
            )
            self.assertEqual("none", backend._auth_mode)
            self.assertEqual("", backend._token)
            self.assertEqual("jarvis", backend._workspace_id)
            self.assertEqual(10, backend._timeout)
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_env_vars_are_resolved_when_no_explicit_args(self):
        import os

        saved = {
            key: os.environ.get(key)
            for key in (
                "HONCHO_BASE_URL",
                "HONCHO_AUTH_MODE",
                "HONCHO_TOKEN",
                "HONCHO_WORKSPACE_ID",
                "HONCHO_TIMEOUT_SECONDS",
            )
        }
        os.environ["HONCHO_BASE_URL"] = "http://env-host:9000"
        os.environ["HONCHO_AUTH_MODE"] = "bearer"
        os.environ["HONCHO_TOKEN"] = "env-token"
        os.environ["HONCHO_WORKSPACE_ID"] = "env-ws"
        os.environ["HONCHO_TIMEOUT_SECONDS"] = "5"
        try:
            backend = HonchoBackend()
            self.assertEqual("http://env-host:9000", backend._base_url)
            self.assertEqual("bearer", backend._auth_mode)
            self.assertEqual("env-token", backend._token)
            self.assertEqual("env-ws", backend._workspace_id)
            self.assertEqual(5, backend._timeout)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_explicit_args_override_env(self):
        import os

        saved = os.environ.get("HONCHO_BASE_URL")
        os.environ["HONCHO_BASE_URL"] = "http://env-host:9000"
        try:
            backend = HonchoBackend(base_url="http://explicit:1234")
            self.assertEqual("http://explicit:1234", backend._base_url)
        finally:
            if saved is None:
                os.environ.pop("HONCHO_BASE_URL", None)
            else:
                os.environ["HONCHO_BASE_URL"] = saved

    def test_auth_mode_defaults_to_bearer_when_token_present_no_explicit_mode(self):
        backend = HonchoBackend(base_url="http://x", token="tok", auth_mode=None)
        self.assertEqual("bearer", backend._auth_mode)


class HonchoAdapterCapabilitiesTests(unittest.TestCase):
    def test_verbs_is_exactly_reflect(self):
        backend = HonchoBackend(base_url="http://x")
        capabilities = backend.capabilities()
        self.assertEqual(frozenset({"reflect"}), capabilities.verbs)
        self.assertNotIn("store", capabilities.verbs)
        self.assertNotIn("search", capabilities.verbs)

    def test_namespaces_is_user_master_only(self):
        backend = HonchoBackend(base_url="http://x")
        self.assertEqual(("/user/master",), backend.capabilities().namespaces)

    def test_name_is_honcho(self):
        backend = HonchoBackend(base_url="http://x")
        self.assertEqual("honcho", backend.capabilities().name)

    def test_is_reflective_backend_not_memory_backend(self):
        backend = HonchoBackend(base_url="http://x")
        self.assertIsInstance(backend, ReflectiveBackend)
        self.assertNotIsInstance(backend, MemoryBackend)


class PeerRefTests(unittest.TestCase):
    def test_valid_user_master_namespace_maps_to_workspace_and_peer(self):
        backend = HonchoBackend(base_url="http://x", workspace_id="jarvis")
        workspace_id, peer_id = backend._peer_ref("/user/master")
        self.assertEqual("jarvis", workspace_id)
        self.assertEqual("master", peer_id)

    def test_traversal_namespace_raises_value_error(self):
        backend = HonchoBackend(base_url="http://x")
        with self.assertRaises(ValueError):
            backend._peer_ref("/user/../../etc/passwd")

    def test_wildcard_namespace_raises_value_error(self):
        backend = HonchoBackend(base_url="http://x")
        with self.assertRaises(ValueError):
            backend._peer_ref("/user/*")

    def test_result_matches_id_charset(self):
        import re

        backend = HonchoBackend(base_url="http://x", workspace_id="Jarvis_Ws")
        workspace_id, peer_id = backend._peer_ref("/user/master")
        self.assertRegex(workspace_id, r"^[a-z0-9][a-z0-9_-]*$")
        self.assertRegex(peer_id, r"^[a-z0-9][a-z0-9_-]*$")


class StubTransport:
    """Records every (method, url, headers, body) call and answers from a
    queue of (status, payload) responses, one per call."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        status, payload = self._responses.pop(0)
        if isinstance(payload, (bytes, type(None))):
            raw = payload or b""
        else:
            raw = json.dumps(payload).encode("utf-8")
        return status, raw


class HonchoAdapterReflectTests(unittest.TestCase):
    def test_dialectic_2xx_with_content_returns_ready_with_conclusions(self):
        transport = StubTransport([(200, {"content": "the user prefers dark mode"})])
        backend = HonchoBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/user/master", role="jarvis", query="preferences?")
        )

        self.assertEqual("ready", result.status)
        self.assertEqual("honcho", result.backend)
        self.assertEqual(1, len(result.conclusions))
        self.assertEqual("the user prefers dark mode", result.conclusions[0].content)
        self.assertEqual("/user/master", result.conclusions[0].namespace)
        self.assertEqual("honcho", result.conclusions[0].backend)
        method, url, _headers, body = transport.calls[0]
        self.assertEqual("POST", method)
        self.assertIn("/v3/workspaces/jarvis/peers/master/chat", url)
        self.assertEqual("preferences?", json.loads(body)["query"])

    def test_202_status_returns_pending_never_fabricated(self):
        transport = StubTransport([(202, b"")])
        backend = HonchoBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/user/master", role="jarvis", query="?")
        )

        self.assertEqual("pending", result.status)
        self.assertEqual((), result.conclusions)

    def test_2xx_empty_body_returns_pending(self):
        transport = StubTransport([(200, b"")])
        backend = HonchoBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/user/master", role="jarvis", query="?")
        )

        self.assertEqual("pending", result.status)

    def test_bearer_header_present_when_auth_mode_bearer(self):
        transport = StubTransport([(200, {"content": "x"})])
        backend = HonchoBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="secret-tok"
        )
        backend.reflect(ReflectRequest(namespace="/user/master", role="jarvis"))
        _method, _url, headers, _body = transport.calls[0]
        self.assertEqual("Bearer secret-tok", headers.get("Authorization"))

    def test_authorization_header_absent_when_auth_mode_none(self):
        transport = StubTransport([(200, {"content": "x"})])
        backend = HonchoBackend(
            transport=transport, base_url="http://x", auth_mode="none", token=""
        )
        backend.reflect(ReflectRequest(namespace="/user/master", role="jarvis"))
        _method, _url, headers, _body = transport.calls[0]
        self.assertNotIn("Authorization", headers)


class HonchoAdapterDegradationTests(unittest.TestCase):
    def test_connection_error_raises_backend_unavailable(self):
        def failing_transport(method, url, headers, body):
            raise OSError("connection refused")

        backend = HonchoBackend(transport=failing_transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.reflect(ReflectRequest(namespace="/user/master", role="jarvis"))

    def test_non_2xx_status_raises_backend_unavailable(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = HonchoBackend(transport=transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.reflect(ReflectRequest(namespace="/user/master", role="jarvis"))

    def test_malformed_json_response_raises_backend_unavailable(self):
        def transport(method, url, headers, body):
            return 200, b"not json{{"

        backend = HonchoBackend(transport=transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.reflect(ReflectRequest(namespace="/user/master", role="jarvis"))

    def test_no_other_exception_type_escapes_on_failure(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = HonchoBackend(transport=transport, base_url="http://x")
        try:
            backend.reflect(ReflectRequest(namespace="/user/master", role="jarvis"))
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.fail(f"unexpected exception type escaped: {type(exc)}: {exc}")

    def test_health_returns_ok_on_2xx(self):
        transport = StubTransport([(200, {})])
        backend = HonchoBackend(transport=transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.OK, health.status)

    def test_health_returns_down_on_non_2xx_and_never_raises(self):
        transport = StubTransport([(503, {"error": "down"})])
        backend = HonchoBackend(transport=transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.DOWN, health.status)

    def test_health_never_raises_on_connection_error(self):
        def failing_transport(method, url, headers, body):
            raise OSError("connection refused")

        backend = HonchoBackend(transport=failing_transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.DOWN, health.status)


class HonchoAdapterSecretHandlingTests(unittest.TestCase):
    def test_token_never_appears_in_backend_unavailable_reason(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = HonchoBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="super-secret-token"
        )
        try:
            backend.reflect(ReflectRequest(namespace="/user/master", role="jarvis"))
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError as exc:
            self.assertNotIn("super-secret-token", exc.reason)

    def test_token_never_appears_in_health_down_reason(self):
        transport = StubTransport([(503, {"error": "down"})])
        backend = HonchoBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="super-secret-token"
        )
        health = backend.health()
        self.assertNotIn("super-secret-token", health.reason)


if __name__ == "__main__":
    unittest.main()
