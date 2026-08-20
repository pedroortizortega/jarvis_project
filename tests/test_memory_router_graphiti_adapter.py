import importlib
import json
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "hermes-native" / "memory-router" / "src")
)

from memory_router.app import Dispatcher, DispatchError
from memory_router.backends.cognee import CogneeBackend
from memory_router.backends.graphiti import GraphitiBackend
from memory_router.backends.honcho import HonchoBackend
from memory_router.contracts import (
    BackendUnavailableError,
    HealthStatus,
    MemoryBackend,
    ReflectRequest,
    ReflectiveBackend,
)
from memory_router.journal import Journal
from memory_router.namespaces import NamespaceError, validate_namespace
from memory_router.registry import Registry


class GraphitiAdapterProtocolTests(unittest.TestCase):
    def test_zero_arg_construction_succeeds(self):
        backend = GraphitiBackend()
        self.assertIsInstance(backend, GraphitiBackend)

    def test_is_reflective_backend_not_memory_backend(self):
        backend = GraphitiBackend(base_url="http://x")
        self.assertIsInstance(backend, ReflectiveBackend)
        self.assertNotIsInstance(backend, MemoryBackend)


class GraphitiAdapterConfigTests(unittest.TestCase):
    def test_defaults_when_no_env_and_no_explicit_args(self):
        import os

        saved = {
            key: os.environ.pop(key, None)
            for key in (
                "GRAPHITI_BASE_URL",
                "GRAPHITI_AUTH_MODE",
                "GRAPHITI_TOKEN",
                "GRAPHITI_GROUP_PREFIX",
                "GRAPHITI_TIMEOUT_SECONDS",
                "GRAPHITI_MAX_FACTS",
            )
        }
        try:
            backend = GraphitiBackend()
            self.assertEqual(
                "http://graphiti.mcps.svc.cluster.local:8000", backend._base_url
            )
            self.assertEqual("none", backend._auth_mode)
            self.assertEqual("", backend._token)
            self.assertEqual("jarvis-", backend._group_prefix)
            self.assertEqual(10, backend._timeout)
            self.assertEqual(10, backend._max_facts)
        finally:
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

    def test_env_vars_are_resolved_when_no_explicit_args(self):
        import os

        saved = {
            key: os.environ.get(key)
            for key in (
                "GRAPHITI_BASE_URL",
                "GRAPHITI_AUTH_MODE",
                "GRAPHITI_TOKEN",
                "GRAPHITI_GROUP_PREFIX",
                "GRAPHITI_TIMEOUT_SECONDS",
                "GRAPHITI_MAX_FACTS",
            )
        }
        os.environ["GRAPHITI_BASE_URL"] = "http://env-host:9000"
        os.environ["GRAPHITI_AUTH_MODE"] = "bearer"
        os.environ["GRAPHITI_TOKEN"] = "env-token"
        os.environ["GRAPHITI_GROUP_PREFIX"] = "env-"
        os.environ["GRAPHITI_TIMEOUT_SECONDS"] = "5"
        os.environ["GRAPHITI_MAX_FACTS"] = "3"
        try:
            backend = GraphitiBackend()
            self.assertEqual("http://env-host:9000", backend._base_url)
            self.assertEqual("bearer", backend._auth_mode)
            self.assertEqual("env-token", backend._token)
            self.assertEqual("env-", backend._group_prefix)
            self.assertEqual(5, backend._timeout)
            self.assertEqual(3, backend._max_facts)
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_explicit_args_override_env(self):
        import os

        saved = os.environ.get("GRAPHITI_BASE_URL")
        os.environ["GRAPHITI_BASE_URL"] = "http://env-host:9000"
        try:
            backend = GraphitiBackend(base_url="http://explicit:1234")
            self.assertEqual("http://explicit:1234", backend._base_url)
        finally:
            if saved is None:
                os.environ.pop("GRAPHITI_BASE_URL", None)
            else:
                os.environ["GRAPHITI_BASE_URL"] = saved

    def test_auth_mode_defaults_to_bearer_when_token_present_no_explicit_mode(self):
        backend = GraphitiBackend(base_url="http://x", token="tok", auth_mode=None)
        self.assertEqual("bearer", backend._auth_mode)


class GraphitiAdapterCapabilitiesTests(unittest.TestCase):
    def test_verbs_is_exactly_reflect(self):
        backend = GraphitiBackend(base_url="http://x")
        capabilities = backend.capabilities()
        self.assertEqual(frozenset({"reflect"}), capabilities.verbs)
        self.assertNotIn("store", capabilities.verbs)
        self.assertNotIn("search", capabilities.verbs)

    def test_namespaces_is_global_and_agents_wildcard(self):
        backend = GraphitiBackend(base_url="http://x")
        self.assertEqual(("/global", "/agents/*"), backend.capabilities().namespaces)

    def test_name_is_graphiti(self):
        backend = GraphitiBackend(base_url="http://x")
        self.assertEqual("graphiti", backend.capabilities().name)

    def test_hierarchical_search_is_false(self):
        backend = GraphitiBackend(base_url="http://x")
        self.assertFalse(backend.capabilities().hierarchical_search)


class GroupIdTests(unittest.TestCase):
    def test_global_maps_to_fixed_shared_group(self):
        backend = GraphitiBackend(base_url="http://x")
        self.assertEqual("jarvis-global", backend._group_id("/global"))

    def test_agent_namespace_maps_to_prefixed_group_with_infix(self):
        backend = GraphitiBackend(base_url="http://x")
        self.assertEqual("jarvis-agent-scientist", backend._group_id("/agents/scientist"))

    def test_prefix_override_honored(self):
        backend = GraphitiBackend(base_url="http://x", group_prefix="acme-")
        self.assertEqual("acme-global", backend._group_id("/global"))
        self.assertEqual("acme-agent-scientist", backend._group_id("/agents/scientist"))

    def test_agents_global_does_not_collide_with_global_group(self):
        # D-02: the "agent-" infix keeps the mapping injective.
        backend = GraphitiBackend(base_url="http://x")
        self.assertEqual("jarvis-global", backend._group_id("/global"))
        self.assertEqual("jarvis-agent-global", backend._group_id("/agents/global"))
        self.assertNotEqual(
            backend._group_id("/global"), backend._group_id("/agents/global")
        )

    def test_two_distinct_agent_namespaces_never_collide(self):
        backend = GraphitiBackend(base_url="http://x")
        self.assertEqual("jarvis-agent-alpha", backend._group_id("/agents/alpha"))
        self.assertEqual("jarvis-agent-beta", backend._group_id("/agents/beta"))
        self.assertNotEqual(
            backend._group_id("/agents/alpha"), backend._group_id("/agents/beta")
        )

    def test_fail_closed_cases_raise_backend_unavailable_never_rewrite(self):
        backend = GraphitiBackend(base_url="http://x")
        illegal_namespaces = [
            "/agents/..",
            "/agents/*",
            "/agents/a?b",
            "/agents/a/b",
            "/agents/Foo",
            "/agents/a.b",
            "/agents/",
        ]
        for namespace in illegal_namespaces:
            with self.subTest(namespace=namespace):
                with self.assertRaises(BackendUnavailableError):
                    backend._group_id(namespace)

    def test_uppercase_and_dot_variants_fail_closed_never_collide(self):
        # D-03: reject, never rewrite. Foo must NOT collide with foo.
        backend = GraphitiBackend(base_url="http://x")
        self.assertEqual("jarvis-agent-foo", backend._group_id("/agents/foo"))
        with self.assertRaises(BackendUnavailableError):
            backend._group_id("/agents/Foo")
        with self.assertRaises(BackendUnavailableError):
            backend._group_id("/agents/a.b")

    def test_malformed_group_prefix_fails_closed_after_revalidation(self):
        backend = GraphitiBackend(base_url="http://x", group_prefix="Bad_Prefix-")
        with self.assertRaises(BackendUnavailableError):
            backend._group_id("/global")
        with self.assertRaises(BackendUnavailableError):
            backend._group_id("/agents/scientist")

    def test_rejection_never_raises_value_error(self):
        backend = GraphitiBackend(base_url="http://x")
        try:
            backend._group_id("/agents/Foo")
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError:
            pass
        except ValueError:
            self.fail("_group_id must raise BackendUnavailableError, not ValueError (D-04)")

    def test_namespace_outside_global_and_agents_fails_closed(self):
        backend = GraphitiBackend(base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend._group_id("/user/master")
        with self.assertRaises(BackendUnavailableError):
            backend._group_id("/projects/x")


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
        raise AssertionError("transport must not be invoked for a fail-closed group mapping")


class GraphitiAdapterReflectTests(unittest.TestCase):
    def test_2xx_with_facts_returns_ready_with_one_unscored_conclusion_per_fact(self):
        transport = StubTransport(
            [(200, {"facts": [{"fact": "alpha likes tea"}, {"fact": "alpha works remotely"}]})]
        )
        backend = GraphitiBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/agents/alpha", role="jarvis", query="preferences?")
        )

        self.assertEqual("ready", result.status)
        self.assertEqual("graphiti", result.backend)
        self.assertEqual(2, len(result.conclusions))
        self.assertEqual("alpha likes tea", result.conclusions[0].content)
        self.assertEqual("alpha works remotely", result.conclusions[1].content)
        for conclusion in result.conclusions:
            self.assertEqual("/agents/alpha", conclusion.namespace)
            self.assertEqual("graphiti", conclusion.backend)
            self.assertEqual(0.0, conclusion.confidence)

        method, url, _headers, body = transport.calls[0]
        self.assertEqual("POST", method)
        self.assertIn("/search/facts", url)
        payload = json.loads(body)
        self.assertEqual("preferences?", payload["query"])
        self.assertEqual(["jarvis-agent-alpha"], payload["group_ids"])
        self.assertEqual(1, len(payload["group_ids"]))
        self.assertEqual(10, payload["max_facts"])

    def test_mixed_valid_and_expired_facts_only_valid_included(self):
        transport = StubTransport(
            [
                (
                    200,
                    {
                        "facts": [
                            {"fact": "currently valid fact"},
                            {"fact": "expired fact", "invalid_at": "2020-01-01T00:00:00Z"},
                        ]
                    },
                )
            ]
        )
        backend = GraphitiBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/global", role="jarvis", query="?")
        )

        self.assertEqual("ready", result.status)
        self.assertEqual(1, len(result.conclusions))
        self.assertEqual("currently valid fact", result.conclusions[0].content)

    def test_all_facts_expired_returns_empty_never_ready_never_pending(self):
        transport = StubTransport(
            [(200, {"facts": [{"fact": "old fact", "invalid_at": "2020-01-01T00:00:00Z"}]})]
        )
        backend = GraphitiBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/global", role="jarvis", query="?")
        )

        self.assertEqual("empty", result.status)
        self.assertNotEqual("ready", result.status)
        self.assertNotEqual("pending", result.status)
        self.assertEqual((), result.conclusions)

    def test_empty_facts_list_returns_empty(self):
        transport = StubTransport([(200, {"facts": []})])
        backend = GraphitiBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/global", role="jarvis", query="?")
        )

        self.assertEqual("empty", result.status)
        self.assertEqual((), result.conclusions)

    def test_absent_facts_key_returns_empty(self):
        transport = StubTransport([(200, {})])
        backend = GraphitiBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/global", role="jarvis", query="?")
        )

        self.assertEqual("empty", result.status)
        self.assertEqual((), result.conclusions)

    def test_blank_fact_text_is_dropped_and_may_empty_result(self):
        transport = StubTransport([(200, {"facts": [{"fact": "   \n\t  "}]})])
        backend = GraphitiBackend(transport=transport, base_url="http://x")

        result = backend.reflect(
            ReflectRequest(namespace="/global", role="jarvis", query="?")
        )

        self.assertEqual("empty", result.status)
        self.assertEqual((), result.conclusions)

    def test_illegal_group_mapping_raises_before_any_http_call(self):
        backend = GraphitiBackend(transport=FailIfCalledTransport(), base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.reflect(
                ReflectRequest(namespace="/agents/Foo", role="jarvis", query="?")
            )


class GraphitiAdapterDegradationTests(unittest.TestCase):
    def test_connection_error_raises_backend_unavailable(self):
        def failing_transport(method, url, headers, body):
            raise OSError("connection refused")

        backend = GraphitiBackend(transport=failing_transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.reflect(ReflectRequest(namespace="/global", role="jarvis"))

    def test_non_2xx_status_raises_backend_unavailable(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = GraphitiBackend(transport=transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.reflect(ReflectRequest(namespace="/global", role="jarvis"))

    def test_malformed_json_response_raises_backend_unavailable(self):
        def transport(method, url, headers, body):
            return 200, b"not json{{"

        backend = GraphitiBackend(transport=transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.reflect(ReflectRequest(namespace="/global", role="jarvis"))

    def test_no_other_exception_type_escapes_on_failure(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = GraphitiBackend(transport=transport, base_url="http://x")
        try:
            backend.reflect(ReflectRequest(namespace="/global", role="jarvis"))
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.fail(f"unexpected exception type escaped: {type(exc)}: {exc}")

    def test_health_returns_ok_on_2xx(self):
        transport = StubTransport([(200, {})])
        backend = GraphitiBackend(transport=transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.OK, health.status)

    def test_health_returns_down_on_non_2xx_and_never_raises(self):
        transport = StubTransport([(503, {"error": "down"})])
        backend = GraphitiBackend(transport=transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.DOWN, health.status)

    def test_health_never_raises_on_connection_error(self):
        def failing_transport(method, url, headers, body):
            raise OSError("connection refused")

        backend = GraphitiBackend(transport=failing_transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.DOWN, health.status)

    def test_health_uses_get_healthz(self):
        transport = StubTransport([(200, {})])
        backend = GraphitiBackend(transport=transport, base_url="http://x")
        backend.health()
        method, url, _headers, _body = transport.calls[0]
        self.assertEqual("GET", method)
        self.assertIn("/healthz", url)


class GraphitiAdapterSecretHandlingTests(unittest.TestCase):
    def test_token_never_appears_in_backend_unavailable_reason(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = GraphitiBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="super-secret-token"
        )
        try:
            backend.reflect(ReflectRequest(namespace="/global", role="jarvis"))
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError as exc:
            self.assertNotIn("super-secret-token", exc.reason)

    def test_token_never_appears_in_health_down_reason(self):
        transport = StubTransport([(503, {"error": "down"})])
        backend = GraphitiBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="super-secret-token"
        )
        health = backend.health()
        self.assertNotIn("super-secret-token", health.reason)

    def test_group_rejection_reason_never_echoes_namespace_or_token(self):
        backend = GraphitiBackend(
            transport=FailIfCalledTransport(),
            base_url="http://x",
            auth_mode="bearer",
            token="super-secret-token",
        )
        try:
            backend.reflect(
                ReflectRequest(namespace="/agents/Foo", role="jarvis", query="?")
            )
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError as exc:
            self.assertNotIn("super-secret-token", exc.reason)
            self.assertNotIn("/agents/Foo", exc.reason)

    def test_bearer_header_present_when_auth_mode_bearer(self):
        transport = StubTransport([(200, {"facts": [{"fact": "x"}]})])
        backend = GraphitiBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="secret-tok"
        )
        backend.reflect(ReflectRequest(namespace="/global", role="jarvis"))
        _method, _url, headers, _body = transport.calls[0]
        self.assertEqual("Bearer secret-tok", headers.get("Authorization"))

    def test_authorization_header_absent_when_auth_mode_none(self):
        transport = StubTransport([(200, {"facts": [{"fact": "x"}]})])
        backend = GraphitiBackend(
            transport=transport, base_url="http://x", auth_mode="none", token=""
        )
        backend.reflect(ReflectRequest(namespace="/global", role="jarvis"))
        _method, _url, headers, _body = transport.calls[0]
        self.assertNotIn("Authorization", headers)


class GraphitiAdapterOutboundConstructionTests(unittest.TestCase):
    def test_hostile_query_appears_only_in_json_body_never_url_or_headers(self):
        transport = StubTransport([(200, {"facts": [{"fact": "x"}]})])
        backend = GraphitiBackend(transport=transport, base_url="http://x")
        hostile_query = "?&<script>ctrl\x00chars</script>"
        backend.reflect(
            ReflectRequest(namespace="/global", role="jarvis", query=hostile_query)
        )
        _method, url, headers, body = transport.calls[0]
        self.assertNotIn(hostile_query, url)
        for value in headers.values():
            self.assertNotIn(hostile_query, str(value))
        payload = json.loads(body)
        self.assertEqual(hostile_query, payload["query"])

    def test_timeout_always_set(self):
        backend = GraphitiBackend(base_url="http://x", timeout=7)
        self.assertEqual(7, backend._timeout)


class GraphitiNamespaceSelectionTests(unittest.TestCase):
    def test_registry_selects_graphiti_for_global(self):
        registry = Registry(backends=[GraphitiBackend(base_url="http://x")])
        selected = registry.backends_for(verb="reflect", namespace="/global")
        self.assertEqual(1, len(selected))
        self.assertEqual("graphiti", selected[0].capabilities().name)

    def test_registry_selects_graphiti_for_agents(self):
        registry = Registry(backends=[GraphitiBackend(base_url="http://x")])
        selected = registry.backends_for(verb="reflect", namespace="/agents/foo")
        self.assertEqual(1, len(selected))
        self.assertEqual("graphiti", selected[0].capabilities().name)

    def test_registry_selects_nothing_for_user_master(self):
        registry = Registry(backends=[GraphitiBackend(base_url="http://x")])
        self.assertEqual(
            [], registry.backends_for(verb="reflect", namespace="/user/master")
        )

    def test_registry_selects_nothing_for_projects(self):
        registry = Registry(backends=[GraphitiBackend(base_url="http://x")])
        self.assertEqual(
            [], registry.backends_for(verb="reflect", namespace="/projects/x")
        )


class ThreeWayCoexistenceTests(unittest.TestCase):
    def test_each_validated_namespace_selects_at_most_one_of_the_three(self):
        registry = Registry(
            backends=[
                HonchoBackend(base_url="http://h"),
                CogneeBackend(base_url="http://c"),
                GraphitiBackend(base_url="http://g"),
            ]
        )
        namespaces = ["/user/master", "/projects/foo", "/global", "/agents/foo"]
        for namespace in namespaces:
            with self.subTest(namespace=namespace):
                selected = registry.backends_for(verb="reflect", namespace=namespace)
                self.assertLessEqual(len(selected), 1)

    def test_user_master_still_selects_only_honcho(self):
        registry = Registry(
            backends=[
                HonchoBackend(base_url="http://h"),
                CogneeBackend(base_url="http://c"),
                GraphitiBackend(base_url="http://g"),
            ]
        )
        selected = registry.backends_for(verb="reflect", namespace="/user/master")
        self.assertEqual(["honcho"], [b.capabilities().name for b in selected])

    def test_projects_still_selects_only_cognee(self):
        registry = Registry(
            backends=[
                HonchoBackend(base_url="http://h"),
                CogneeBackend(base_url="http://c"),
                GraphitiBackend(base_url="http://g"),
            ]
        )
        selected = registry.backends_for(verb="reflect", namespace="/projects/foo")
        self.assertEqual(["cognee"], [b.capabilities().name for b in selected])

    def test_global_selects_only_graphiti(self):
        registry = Registry(
            backends=[
                HonchoBackend(base_url="http://h"),
                CogneeBackend(base_url="http://c"),
                GraphitiBackend(base_url="http://g"),
            ]
        )
        selected = registry.backends_for(verb="reflect", namespace="/global")
        self.assertEqual(["graphiti"], [b.capabilities().name for b in selected])

    def test_agents_selects_only_graphiti(self):
        registry = Registry(
            backends=[
                HonchoBackend(base_url="http://h"),
                CogneeBackend(base_url="http://c"),
                GraphitiBackend(base_url="http://g"),
            ]
        )
        selected = registry.backends_for(verb="reflect", namespace="/agents/foo")
        self.assertEqual(["graphiti"], [b.capabilities().name for b in selected])


class EntryPointRegistrationTests(unittest.TestCase):
    """The package is not pip-installed in this environment (see
    test_memory_router_registry.py — Registry is exercised with explicit
    backend lists everywhere), so `importlib.metadata.entry_points()`
    returns nothing here. This test instead parses the exact dotted paths
    declared under `[project.entry-points."memory_router.backends"]` in
    pyproject.toml and confirms each one imports and constructs with zero
    args without error — the same guarantee `Registry._load_entry_points()`
    depends on once the package is actually installed."""

    def test_graphiti_entry_point_loads_alongside_existing_backends(self):
        pyproject_path = (
            Path(__file__).resolve().parent.parent
            / "hermes-native"
            / "memory-router"
            / "pyproject.toml"
        )
        with open(pyproject_path, "rb") as handle:
            data = tomllib.load(handle)

        entry_points = data["project"]["entry-points"]["memory_router.backends"]
        self.assertIn("graphiti", entry_points)
        self.assertEqual(
            "memory_router.backends.graphiti:GraphitiBackend", entry_points["graphiti"]
        )

        expected_names = {"engram", "hindsight", "honcho", "cognee", "graphiti"}
        self.assertTrue(expected_names.issubset(entry_points.keys()))

        for name in expected_names:
            with self.subTest(name=name):
                module_path, _, class_name = entry_points[name].partition(":")
                module = importlib.import_module(module_path)
                backend_class = getattr(module, class_name)
                backend = backend_class()
                self.assertIsNotNone(backend.capabilities())


class NestedAgentNamespaceValidationTests(unittest.TestCase):
    """F-2 (design.md): nested /agents/a/b is unreachable at the existing
    validation layer, before permissions/registry/adapter. Confirms the
    finding rather than adding adapter-level nested-namespace handling."""

    def test_validate_namespace_rejects_nested_agent_namespace(self):
        with self.assertRaises(NamespaceError):
            validate_namespace("/agents/a/b")


_CN_TO_IDENTITY = {"hermes-gateway": "hermes-gateway"}
_BEARER_BY_IDENTITY = {"hermes-gateway": "token-hg"}


class DispatcherLevelNestedNamespaceTests(unittest.TestCase):
    """Task 6.2: dispatcher-level confirmation of F-2 — a reflect request on
    a nested /agents/a/b namespace dies at validate_namespace, before the
    Graphiti adapter is ever reached, and returns 400 invalid_namespace."""

    def test_reflect_on_nested_agent_namespace_returns_400_invalid_namespace(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = GraphitiBackend(
                transport=FailIfCalledTransport(), base_url="http://x"
            )
            registry = Registry(backends=[backend])
            journal = Journal(Path(directory) / "journal.ndjson")
            dispatcher = Dispatcher(
                registry=registry,
                journal=journal,
                cn_to_identity=_CN_TO_IDENTITY,
                bearer_by_identity=_BEARER_BY_IDENTITY,
            )
            with self.assertRaises(DispatchError) as ctx:
                dispatcher.reflect(
                    cn="hermes-gateway", bearer="token-hg", role="jarvis",
                    namespace="/agents/a/b", query="x",
                )
        self.assertEqual(400, ctx.exception.status)
        self.assertEqual("invalid_namespace", ctx.exception.error)


if __name__ == "__main__":
    unittest.main()
