import json
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "hermes-native" / "memory-router" / "src")
)

from memory_router.backends.cognee import CogneeBackend
from memory_router.backends.honcho import HonchoBackend
from memory_router.contracts import (
    BackendUnavailableError,
    HealthStatus,
    MemoryBackend,
    ReflectRequest,
    ReflectiveBackend,
)
from memory_router.registry import Registry


class CogneeAdapterProtocolTests(unittest.TestCase):
    def test_zero_arg_construction_succeeds(self):
        backend = CogneeBackend()
        self.assertIsInstance(backend, CogneeBackend)

    def test_is_reflective_backend_not_memory_backend(self):
        backend = CogneeBackend(base_url="http://x")
        self.assertIsInstance(backend, ReflectiveBackend)
        self.assertNotIsInstance(backend, MemoryBackend)


class CogneeAdapterConfigTests(unittest.TestCase):
    def test_defaults_when_no_env_and_no_explicit_args(self):
        import os

        saved = {
            key: os.environ.pop(key, None)
            for key in (
                "COGNEE_BASE_URL",
                "COGNEE_AUTH_MODE",
                "COGNEE_TOKEN",
                "COGNEE_DATASET_PREFIX",
                "COGNEE_TIMEOUT_SECONDS",
            )
        }
        try:
            backend = CogneeBackend()
            self.assertEqual(
                "http://cognee.mcps.svc.cluster.local:8000", backend._base_url
            )
            self.assertEqual("none", backend._auth_mode)
            self.assertEqual("", backend._token)
            self.assertEqual("jarvis-", backend._dataset_prefix)
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
                "COGNEE_BASE_URL",
                "COGNEE_AUTH_MODE",
                "COGNEE_TOKEN",
                "COGNEE_DATASET_PREFIX",
                "COGNEE_TIMEOUT_SECONDS",
            )
        }
        os.environ["COGNEE_BASE_URL"] = "http://env-host:9000"
        os.environ["COGNEE_AUTH_MODE"] = "bearer"
        os.environ["COGNEE_TOKEN"] = "env-token"
        os.environ["COGNEE_DATASET_PREFIX"] = "env-"
        os.environ["COGNEE_TIMEOUT_SECONDS"] = "5"
        try:
            backend = CogneeBackend()
            self.assertEqual("http://env-host:9000", backend._base_url)
            self.assertEqual("bearer", backend._auth_mode)
            self.assertEqual("env-token", backend._token)
            self.assertEqual("env-", backend._dataset_prefix)
            self.assertEqual(5, backend._timeout)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_explicit_args_override_env(self):
        import os

        saved = os.environ.get("COGNEE_BASE_URL")
        os.environ["COGNEE_BASE_URL"] = "http://env-host:9000"
        try:
            backend = CogneeBackend(base_url="http://explicit:1234")
            self.assertEqual("http://explicit:1234", backend._base_url)
        finally:
            if saved is None:
                os.environ.pop("COGNEE_BASE_URL", None)
            else:
                os.environ["COGNEE_BASE_URL"] = saved

    def test_auth_mode_defaults_to_bearer_when_token_present_no_explicit_mode(self):
        backend = CogneeBackend(base_url="http://x", token="tok", auth_mode=None)
        self.assertEqual("bearer", backend._auth_mode)


class CogneeAdapterCapabilitiesTests(unittest.TestCase):
    def test_verbs_is_exactly_reflect(self):
        backend = CogneeBackend(base_url="http://x")
        capabilities = backend.capabilities()
        self.assertEqual(frozenset({"reflect"}), capabilities.verbs)
        self.assertNotIn("store", capabilities.verbs)
        self.assertNotIn("search", capabilities.verbs)

    def test_namespaces_is_projects_wildcard_only(self):
        backend = CogneeBackend(base_url="http://x")
        self.assertEqual(("/projects/*",), backend.capabilities().namespaces)

    def test_name_is_cognee(self):
        backend = CogneeBackend(base_url="http://x")
        self.assertEqual("cognee", backend.capabilities().name)

    def test_hierarchical_search_is_false(self):
        backend = CogneeBackend(base_url="http://x")
        self.assertFalse(backend.capabilities().hierarchical_search)


class DatasetIdTests(unittest.TestCase):
    def test_project_namespace_maps_to_prefixed_dataset(self):
        backend = CogneeBackend(base_url="http://x")
        self.assertEqual("jarvis-hermes", backend._dataset_id("/projects/hermes"))

    def test_prefix_override_honored(self):
        backend = CogneeBackend(base_url="http://x", dataset_prefix="acme-")
        self.assertEqual("acme-hermes", backend._dataset_id("/projects/hermes"))

    def test_two_distinct_namespaces_never_collide(self):
        backend = CogneeBackend(base_url="http://x")
        self.assertEqual(
            "jarvis-alpha", backend._dataset_id("/projects/alpha")
        )
        self.assertEqual(
            "jarvis-beta", backend._dataset_id("/projects/beta")
        )
        self.assertNotEqual(
            backend._dataset_id("/projects/alpha"), backend._dataset_id("/projects/beta")
        )

    def test_fail_closed_cases_raise_backend_unavailable_never_rewrite(self):
        backend = CogneeBackend(base_url="http://x")
        illegal_namespaces = [
            "/projects/..",
            "/projects/*",
            "/projects/a?b",
            "/projects/a/b",
            "/projects/Foo",
            "/projects/a.b",
            "/projects/",
        ]
        for namespace in illegal_namespaces:
            with self.subTest(namespace=namespace):
                with self.assertRaises(BackendUnavailableError):
                    backend._dataset_id(namespace)

    def test_leading_hyphen_fails_closed_with_empty_prefix(self):
        # With no prefix to supply a legal leading char, a project name
        # starting with "-" cannot form a legal dataset id on its own.
        backend = CogneeBackend(base_url="http://x", dataset_prefix="")
        with self.assertRaises(BackendUnavailableError):
            backend._dataset_id("/projects/-leading")

    def test_uppercase_and_lowercase_project_names_fail_closed_never_collide(self):
        # D-03: reject, never rewrite. Foo must NOT collide with foo.
        backend = CogneeBackend(base_url="http://x")
        self.assertEqual("jarvis-foo", backend._dataset_id("/projects/foo"))
        with self.assertRaises(BackendUnavailableError):
            backend._dataset_id("/projects/Foo")

    def test_malformed_dataset_prefix_fails_closed_after_revalidation(self):
        backend = CogneeBackend(base_url="http://x", dataset_prefix="Bad_Prefix-")
        with self.assertRaises(BackendUnavailableError):
            backend._dataset_id("/projects/hermes")

    def test_rejection_never_raises_value_error(self):
        backend = CogneeBackend(base_url="http://x")
        try:
            backend._dataset_id("/projects/Foo")
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError:
            pass
        except ValueError:
            self.fail("_dataset_id must raise BackendUnavailableError, not ValueError (D-04)")

    def test_non_project_namespace_fails_closed(self):
        backend = CogneeBackend(base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend._dataset_id("/user/master")


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


class FailIfCalledTransport:
    def __call__(self, method, url, headers, body):
        raise AssertionError("transport must not be invoked for a fail-closed dataset mapping")


class CogneeAdapterReflectTests(unittest.TestCase):
    def test_2xx_with_answer_returns_ready_with_one_unscored_conclusion(self):
        transport = StubTransport([(200, {"result": "synthesized graph answer"})])
        backend = CogneeBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/projects/hermes", role="jarvis", query="status?")
        )

        self.assertEqual("ready", result.status)
        self.assertEqual("cognee", result.backend)
        self.assertEqual(1, len(result.conclusions))
        self.assertEqual(
            "synthesized graph answer", result.conclusions[0].content
        )
        self.assertEqual("/projects/hermes", result.conclusions[0].namespace)
        self.assertEqual("cognee", result.conclusions[0].backend)
        self.assertEqual(0.0, result.conclusions[0].confidence)

        method, url, _headers, body = transport.calls[0]
        self.assertEqual("POST", method)
        self.assertIn("/recall", url)
        payload = json.loads(body)
        self.assertEqual("status?", payload["query"])
        self.assertEqual("GRAPH_COMPLETION", payload["search_type"])
        self.assertEqual(["jarvis-hermes"], payload["datasets"])

    def test_2xx_empty_answer_returns_empty_not_pending_not_ready(self):
        transport = StubTransport([(200, {"result": ""})])
        backend = CogneeBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/projects/hermes", role="jarvis", query="?")
        )

        self.assertEqual("empty", result.status)
        self.assertNotEqual("pending", result.status)
        self.assertNotEqual("ready", result.status)
        self.assertEqual((), result.conclusions)

    def test_2xx_absent_answer_returns_empty(self):
        transport = StubTransport([(200, {})])
        backend = CogneeBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/projects/hermes", role="jarvis", query="?")
        )

        self.assertEqual("empty", result.status)
        self.assertEqual((), result.conclusions)

    def test_2xx_whitespace_only_answer_returns_empty(self):
        transport = StubTransport([(200, {"result": "   \n\t  "})])
        backend = CogneeBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/projects/hermes", role="jarvis", query="?")
        )

        self.assertEqual("empty", result.status)
        self.assertEqual((), result.conclusions)

    def test_2xx_empty_body_returns_empty(self):
        transport = StubTransport([(200, b"")])
        backend = CogneeBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/projects/hermes", role="jarvis", query="?")
        )

        self.assertEqual("empty", result.status)

    def test_illegal_dataset_mapping_raises_before_any_http_call(self):
        backend = CogneeBackend(transport=FailIfCalledTransport(), base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.reflect(
                ReflectRequest(namespace="/projects/Foo", role="jarvis", query="?")
            )


class CogneeAdapterDegradationTests(unittest.TestCase):
    def test_connection_error_raises_backend_unavailable(self):
        def failing_transport(method, url, headers, body):
            raise OSError("connection refused")

        backend = CogneeBackend(transport=failing_transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.reflect(ReflectRequest(namespace="/projects/hermes", role="jarvis"))

    def test_non_2xx_status_raises_backend_unavailable(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = CogneeBackend(transport=transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.reflect(ReflectRequest(namespace="/projects/hermes", role="jarvis"))

    def test_malformed_json_response_raises_backend_unavailable(self):
        def transport(method, url, headers, body):
            return 200, b"not json{{"

        backend = CogneeBackend(transport=transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.reflect(ReflectRequest(namespace="/projects/hermes", role="jarvis"))

    def test_no_other_exception_type_escapes_on_failure(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = CogneeBackend(transport=transport, base_url="http://x")
        try:
            backend.reflect(ReflectRequest(namespace="/projects/hermes", role="jarvis"))
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.fail(f"unexpected exception type escaped: {type(exc)}: {exc}")

    def test_health_returns_ok_on_2xx(self):
        transport = StubTransport([(200, {})])
        backend = CogneeBackend(transport=transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.OK, health.status)

    def test_health_returns_down_on_non_2xx_and_never_raises(self):
        transport = StubTransport([(503, {"error": "down"})])
        backend = CogneeBackend(transport=transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.DOWN, health.status)

    def test_health_never_raises_on_connection_error(self):
        def failing_transport(method, url, headers, body):
            raise OSError("connection refused")

        backend = CogneeBackend(transport=failing_transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.DOWN, health.status)


class CogneeAdapterSecretHandlingTests(unittest.TestCase):
    def test_token_never_appears_in_backend_unavailable_reason(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = CogneeBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="super-secret-token"
        )
        try:
            backend.reflect(ReflectRequest(namespace="/projects/hermes", role="jarvis"))
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError as exc:
            self.assertNotIn("super-secret-token", exc.reason)

    def test_token_never_appears_in_health_down_reason(self):
        transport = StubTransport([(503, {"error": "down"})])
        backend = CogneeBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="super-secret-token"
        )
        health = backend.health()
        self.assertNotIn("super-secret-token", health.reason)

    def test_dataset_rejection_reason_never_echoes_namespace_or_token(self):
        backend = CogneeBackend(
            transport=FailIfCalledTransport(),
            base_url="http://x",
            auth_mode="bearer",
            token="super-secret-token",
        )
        try:
            backend.reflect(
                ReflectRequest(namespace="/projects/Foo", role="jarvis", query="?")
            )
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError as exc:
            self.assertNotIn("super-secret-token", exc.reason)
            self.assertNotIn("/projects/Foo", exc.reason)

    def test_bearer_header_present_when_auth_mode_bearer(self):
        transport = StubTransport([(200, {"result": "x"})])
        backend = CogneeBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="secret-tok"
        )
        backend.reflect(ReflectRequest(namespace="/projects/hermes", role="jarvis"))
        _method, _url, headers, _body = transport.calls[0]
        self.assertEqual("Bearer secret-tok", headers.get("Authorization"))

    def test_authorization_header_absent_when_auth_mode_none(self):
        transport = StubTransport([(200, {"result": "x"})])
        backend = CogneeBackend(
            transport=transport, base_url="http://x", auth_mode="none", token=""
        )
        backend.reflect(ReflectRequest(namespace="/projects/hermes", role="jarvis"))
        _method, _url, headers, _body = transport.calls[0]
        self.assertNotIn("Authorization", headers)


class CogneeAdapterOutboundConstructionTests(unittest.TestCase):
    def test_hostile_query_appears_only_in_json_body_never_url_or_headers(self):
        transport = StubTransport([(200, {"result": "x"})])
        backend = CogneeBackend(transport=transport, base_url="http://x")
        hostile_query = "?&<script>ctrl\x00chars</script>"
        backend.reflect(
            ReflectRequest(namespace="/projects/hermes", role="jarvis", query=hostile_query)
        )
        _method, url, headers, body = transport.calls[0]
        self.assertNotIn(hostile_query, url)
        for value in headers.values():
            self.assertNotIn(hostile_query, str(value))
        payload = json.loads(body)
        self.assertEqual(hostile_query, payload["query"])

    def test_timeout_always_set(self):
        backend = CogneeBackend(base_url="http://x", timeout=7)
        self.assertEqual(7, backend._timeout)


class CogneeNamespaceSelectionTests(unittest.TestCase):
    def test_registry_selects_cognee_for_projects_namespace(self):
        registry = Registry(backends=[CogneeBackend(base_url="http://x")])
        selected = registry.backends_for(verb="reflect", namespace="/projects/foo")
        self.assertEqual(1, len(selected))
        self.assertEqual("cognee", selected[0].capabilities().name)

    def test_registry_selects_nothing_for_user_master(self):
        registry = Registry(backends=[CogneeBackend(base_url="http://x")])
        self.assertEqual(
            [], registry.backends_for(verb="reflect", namespace="/user/master")
        )

    def test_registry_selects_nothing_for_global(self):
        registry = Registry(backends=[CogneeBackend(base_url="http://x")])
        self.assertEqual([], registry.backends_for(verb="reflect", namespace="/global"))

    def test_registry_selects_nothing_for_agents(self):
        registry = Registry(backends=[CogneeBackend(base_url="http://x")])
        self.assertEqual(
            [], registry.backends_for(verb="reflect", namespace="/agents/x")
        )


class HonchoCogneeCoexistenceTests(unittest.TestCase):
    def test_user_master_selects_only_honcho(self):
        registry = Registry(
            backends=[HonchoBackend(base_url="http://h"), CogneeBackend(base_url="http://c")]
        )
        selected = registry.backends_for(verb="reflect", namespace="/user/master")
        self.assertEqual(["honcho"], [b.capabilities().name for b in selected])

    def test_projects_selects_only_cognee(self):
        registry = Registry(
            backends=[HonchoBackend(base_url="http://h"), CogneeBackend(base_url="http://c")]
        )
        selected = registry.backends_for(verb="reflect", namespace="/projects/foo")
        self.assertEqual(["cognee"], [b.capabilities().name for b in selected])


if __name__ == "__main__":
    unittest.main()
