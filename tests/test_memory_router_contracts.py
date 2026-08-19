import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "hermes-native" / "orchestration" / "src")
)

from memory_router.contracts import (
    BackendUnavailableError,
    Capabilities,
    Health,
    HealthStatus,
    MemoryBackend,
    SearchHit,
    SearchRequest,
    SearchResult,
    StoreRequest,
    StoreResult,
)


class ContractsTests(unittest.TestCase):
    def test_capabilities_declares_verbs_and_namespaces(self):
        capabilities = Capabilities(
            name="engram",
            verbs=frozenset({"store", "search"}),
            namespaces=("/global", "/projects/*"),
            hierarchical_search=True,
        )
        self.assertEqual("engram", capabilities.name)
        self.assertIn("store", capabilities.verbs)
        self.assertIn("/global", capabilities.namespaces)
        self.assertTrue(capabilities.hierarchical_search)

    def test_capabilities_is_immutable(self):
        capabilities = Capabilities(
            name="engram", verbs=frozenset({"search"}), namespaces=("/global",)
        )
        with self.assertRaises(AttributeError):
            capabilities.name = "other"

    def test_health_status_values(self):
        self.assertEqual("ok", HealthStatus.OK.value)
        self.assertEqual("degraded", HealthStatus.DEGRADED.value)
        self.assertEqual("down", HealthStatus.DOWN.value)

    def test_health_defaults_reason_empty(self):
        health = Health(status=HealthStatus.OK)
        self.assertEqual("", health.reason)

    def test_store_request_and_result_shape(self):
        request = StoreRequest(
            namespace="/projects/lector-ine", role="coder", content="note"
        )
        self.assertEqual("/projects/lector-ine", request.namespace)
        self.assertEqual({}, request.metadata)

        result = StoreResult(status="committed", backend="engram", id="abc-1")
        self.assertEqual("committed", result.status)

    def test_search_request_result_and_hit_shape(self):
        request = SearchRequest(
            namespace="/projects/lector-ine", role="coder", query="deploy"
        )
        self.assertEqual("deploy", request.query)

        hit = SearchHit(
            namespace="/projects/lector-ine", backend="engram", content="found it"
        )
        result = SearchResult(hits=(hit,), unavailable=())
        self.assertEqual(1, len(result.hits))
        self.assertEqual((), result.unavailable)

    def test_memory_backend_protocol_conformance(self):
        class FakeBackend:
            def capabilities(self) -> Capabilities:
                return Capabilities(
                    name="fake", verbs=frozenset({"store"}), namespaces=("/global",)
                )

            def health(self) -> Health:
                return Health(status=HealthStatus.OK)

            def store(self, req: StoreRequest) -> StoreResult:
                return StoreResult(status="committed", backend="fake", id="1")

            def search(self, req: SearchRequest) -> SearchResult:
                return SearchResult(hits=())

        self.assertIsInstance(FakeBackend(), MemoryBackend)

        class NotABackend:
            pass

        self.assertNotIsInstance(NotABackend(), MemoryBackend)


    def test_backend_unavailable_error_carries_reason(self):
        error = BackendUnavailableError("engram", "subprocess exited")
        self.assertEqual("engram", error.backend)
        self.assertIn("subprocess exited", str(error))


if __name__ == "__main__":
    unittest.main()
