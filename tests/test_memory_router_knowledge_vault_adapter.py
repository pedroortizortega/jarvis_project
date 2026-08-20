import json
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "hermes-native" / "memory-router" / "src")
)

from memory_router.backends.knowledge_vault import KnowledgeVaultBackend
from memory_router.contracts import (
    BackendUnavailableError,
    Capabilities,
    Health,
    HealthStatus,
    MemoryBackend,
    SearchOnlyBackend,
    SearchRequest,
    SearchResult,
)
from memory_router.registry import Registry

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_PATH = (
    REPO_ROOT / "hermes-native" / "memory-router" / "src" / "memory_router" / "contracts.py"
)


class MinimalSearchOnlyStub:
    def capabilities(self):
        return Capabilities(
            name="stub", verbs=frozenset({"search"}), namespaces=("/global",),
            hierarchical_search=False,
        )

    def health(self):
        return Health(status=HealthStatus.OK)

    def search(self, req):
        return SearchResult()


class SearchOnlyBackendProtocolTests(unittest.TestCase):
    def test_minimal_stub_satisfies_search_only_backend(self):
        self.assertIsInstance(MinimalSearchOnlyStub(), SearchOnlyBackend)

    def test_minimal_stub_is_not_a_memory_backend(self):
        self.assertNotIsInstance(MinimalSearchOnlyStub(), MemoryBackend)


class MemoryBackendByteIdenticalTests(unittest.TestCase):
    def test_memory_backend_block_matches_origin_main(self):
        result = subprocess.run(
            [
                "git", "show",
                "origin/main:hermes-native/memory-router/src/memory_router/contracts.py",
            ],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        origin_source = result.stdout
        current_source = CONTRACTS_PATH.read_text(encoding="utf-8")

        def extract_memory_backend(source):
            start = source.index("class MemoryBackend")
            rest = source[start:]
            end = rest.index("\n\n\n@dataclass(frozen=True)\nclass ReflectRequest")
            return rest[:end]

        self.assertEqual(
            extract_memory_backend(origin_source), extract_memory_backend(current_source)
        )


class KnowledgeVaultAdapterProtocolTests(unittest.TestCase):
    def test_zero_arg_construction_succeeds(self):
        backend = KnowledgeVaultBackend()
        self.assertIsInstance(backend, KnowledgeVaultBackend)

    def test_is_search_only_backend_not_memory_backend(self):
        backend = KnowledgeVaultBackend(base_url="http://x")
        self.assertIsInstance(backend, SearchOnlyBackend)
        self.assertNotIsInstance(backend, MemoryBackend)

    def test_declares_no_store_or_reflect(self):
        backend = KnowledgeVaultBackend(base_url="http://x")
        self.assertFalse(hasattr(backend, "store"))
        self.assertFalse(hasattr(backend, "reflect"))


class KnowledgeVaultAdapterCapabilitiesTests(unittest.TestCase):
    def test_verbs_is_exactly_search(self):
        backend = KnowledgeVaultBackend(base_url="http://x")
        capabilities = backend.capabilities()
        self.assertEqual(frozenset({"search"}), capabilities.verbs)
        self.assertNotIn("store", capabilities.verbs)
        self.assertNotIn("reflect", capabilities.verbs)

    def test_namespaces_is_global_only(self):
        backend = KnowledgeVaultBackend(base_url="http://x")
        self.assertEqual(("/global",), backend.capabilities().namespaces)

    def test_name_is_knowledge_vault(self):
        backend = KnowledgeVaultBackend(base_url="http://x")
        self.assertEqual("knowledge-vault", backend.capabilities().name)

    def test_hierarchical_search_is_false(self):
        backend = KnowledgeVaultBackend(base_url="http://x")
        self.assertFalse(backend.capabilities().hierarchical_search)


class FailIfCalledTransport:
    def __call__(self, method, url, headers, body):
        raise AssertionError("transport must not be invoked for a non-/global namespace")


class KnowledgeVaultNamespaceGuardTests(unittest.TestCase):
    def test_non_global_namespaces_raise_backend_unavailable_no_http_call(self):
        backend = KnowledgeVaultBackend(transport=FailIfCalledTransport(), base_url="http://x")
        for namespace in ("/user/master", "/projects/x", "/agents/x"):
            with self.subTest(namespace=namespace):
                with self.assertRaises(BackendUnavailableError):
                    backend.search(SearchRequest(namespace=namespace, role="jarvis", query="q"))


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


class KnowledgeVaultAdapterSearchTests(unittest.TestCase):
    def test_round_trip_score_and_content_shape(self):
        transport = StubTransport(
            [(200, {"hits": [
                {"note": "0007-x.md", "title": "Some Title", "excerpt": "excerpt text", "score": 0.83}
            ]})]
        )
        backend = KnowledgeVaultBackend(transport=transport, base_url="http://x")

        result = backend.search(SearchRequest(namespace="/global", role="jarvis", query="q"))

        self.assertEqual(1, len(result.hits))
        hit = result.hits[0]
        self.assertEqual("/global", hit.namespace)
        self.assertEqual("knowledge-vault", hit.backend)
        self.assertEqual("0007-x.md — Some Title\nexcerpt text", hit.content)
        self.assertEqual(0.83, hit.score)
        self.assertNotEqual(0.0, hit.score)

    def test_empty_hits_returns_empty_result_never_fabricated(self):
        transport = StubTransport([(200, {"hits": []})])
        backend = KnowledgeVaultBackend(transport=transport, base_url="http://x")
        result = backend.search(SearchRequest(namespace="/global", role="jarvis", query="q"))
        self.assertEqual((), result.hits)


class KnowledgeVaultAdapterDegradationTests(unittest.TestCase):
    def test_connection_error_raises_backend_unavailable(self):
        def failing_transport(method, url, headers, body):
            raise OSError("connection refused")

        backend = KnowledgeVaultBackend(transport=failing_transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.search(SearchRequest(namespace="/global", role="jarvis", query="q"))

    def test_non_2xx_status_raises_backend_unavailable(self):
        transport = StubTransport([(503, {"error": "index_rebuild_timeout"})])
        backend = KnowledgeVaultBackend(transport=transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.search(SearchRequest(namespace="/global", role="jarvis", query="q"))

    def test_malformed_json_response_raises_backend_unavailable(self):
        def transport(method, url, headers, body):
            return 200, b"not json{{"

        backend = KnowledgeVaultBackend(transport=transport, base_url="http://x")
        with self.assertRaises(BackendUnavailableError):
            backend.search(SearchRequest(namespace="/global", role="jarvis", query="q"))

    def test_no_other_exception_type_escapes_on_failure(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = KnowledgeVaultBackend(transport=transport, base_url="http://x")
        try:
            backend.search(SearchRequest(namespace="/global", role="jarvis", query="q"))
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError:
            pass
        except Exception as exc:  # noqa: BLE001
            self.fail(f"unexpected exception type escaped: {type(exc)}: {exc}")

    def test_health_returns_ok_on_2xx(self):
        transport = StubTransport([(200, {"status": "ok"})])
        backend = KnowledgeVaultBackend(transport=transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.OK, health.status)

    def test_health_returns_down_on_non_2xx_and_never_raises(self):
        transport = StubTransport([(503, {"error": "down"})])
        backend = KnowledgeVaultBackend(transport=transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.DOWN, health.status)

    def test_health_never_raises_on_connection_error(self):
        def failing_transport(method, url, headers, body):
            raise OSError("connection refused")

        backend = KnowledgeVaultBackend(transport=failing_transport, base_url="http://x")
        health = backend.health()
        self.assertEqual(HealthStatus.DOWN, health.status)


class KnowledgeVaultAdapterSecretHandlingTests(unittest.TestCase):
    def test_token_never_appears_in_backend_unavailable_reason(self):
        transport = StubTransport([(500, {"error": "boom"})])
        backend = KnowledgeVaultBackend(
            transport=transport, base_url="http://x", auth_mode="bearer",
            token="super-secret-token",
        )
        try:
            backend.search(SearchRequest(namespace="/global", role="jarvis", query="q"))
            self.fail("expected BackendUnavailableError")
        except BackendUnavailableError as exc:
            self.assertNotIn("super-secret-token", exc.reason)

    def test_token_never_appears_in_health_down_reason(self):
        transport = StubTransport([(503, {"error": "down"})])
        backend = KnowledgeVaultBackend(
            transport=transport, base_url="http://x", auth_mode="bearer",
            token="super-secret-token",
        )
        health = backend.health()
        self.assertNotIn("super-secret-token", health.reason)

    def test_bearer_header_present_when_auth_mode_bearer(self):
        transport = StubTransport([(200, {"hits": []})])
        backend = KnowledgeVaultBackend(
            transport=transport, base_url="http://x", auth_mode="bearer", token="secret-tok"
        )
        backend.search(SearchRequest(namespace="/global", role="jarvis", query="q"))
        _method, _url, headers, _body = transport.calls[0]
        self.assertEqual("Bearer secret-tok", headers.get("Authorization"))

    def test_authorization_header_absent_when_auth_mode_none(self):
        transport = StubTransport([(200, {"hits": []})])
        backend = KnowledgeVaultBackend(
            transport=transport, base_url="http://x", auth_mode="none", token=""
        )
        backend.search(SearchRequest(namespace="/global", role="jarvis", query="q"))
        _method, _url, headers, _body = transport.calls[0]
        self.assertNotIn("Authorization", headers)


class KnowledgeVaultAdapterOutboundConstructionTests(unittest.TestCase):
    def test_hostile_query_appears_only_in_json_body_never_url_or_headers(self):
        transport = StubTransport([(200, {"hits": []})])
        backend = KnowledgeVaultBackend(transport=transport, base_url="http://x")
        hostile_query = "?&<script>ctrl\x00chars</script>"
        backend.search(
            SearchRequest(namespace="/global", role="jarvis", query=hostile_query)
        )
        _method, url, headers, body = transport.calls[0]
        self.assertNotIn(hostile_query, url)
        for value in headers.values():
            self.assertNotIn(hostile_query, str(value))
        payload = json.loads(body)
        self.assertEqual(hostile_query, payload["query"])

    def test_limit_is_always_an_adapter_chosen_int(self):
        transport = StubTransport([(200, {"hits": []})])
        backend = KnowledgeVaultBackend(transport=transport, base_url="http://x", limit=7)
        backend.search(SearchRequest(namespace="/global", role="jarvis", query="q"))
        _method, _url, _headers, body = transport.calls[0]
        payload = json.loads(body)
        self.assertEqual(7, payload["limit"])
        self.assertIsInstance(payload["limit"], int)

    def test_timeout_always_set(self):
        backend = KnowledgeVaultBackend(base_url="http://x", timeout=7)
        self.assertEqual(7, backend._timeout)


class KnowledgeVaultNamespaceSelectionTests(unittest.TestCase):
    def test_registry_selects_knowledge_vault_for_global(self):
        registry = Registry(backends=[KnowledgeVaultBackend(base_url="http://x")])
        selected = registry.backends_for(verb="search", namespace="/global")
        self.assertEqual(1, len(selected))
        self.assertEqual("knowledge-vault", selected[0].capabilities().name)

    def test_registry_selects_nothing_for_projects(self):
        registry = Registry(backends=[KnowledgeVaultBackend(base_url="http://x")])
        self.assertEqual(
            [], registry.backends_for(verb="search", namespace="/projects/x")
        )

    def test_registry_selects_nothing_for_agents(self):
        registry = Registry(backends=[KnowledgeVaultBackend(base_url="http://x")])
        self.assertEqual([], registry.backends_for(verb="search", namespace="/agents/x"))

    def test_registry_selects_nothing_for_user_master(self):
        registry = Registry(backends=[KnowledgeVaultBackend(base_url="http://x")])
        self.assertEqual([], registry.backends_for(verb="search", namespace="/user/master"))

    def test_registry_selects_nothing_for_store_or_reflect_on_global(self):
        registry = Registry(backends=[KnowledgeVaultBackend(base_url="http://x")])
        self.assertEqual([], registry.backends_for(verb="store", namespace="/global"))
        self.assertEqual([], registry.backends_for(verb="reflect", namespace="/global"))


if __name__ == "__main__":
    unittest.main()
