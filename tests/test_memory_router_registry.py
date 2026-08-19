import sys
import unittest
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent / "hermes-native" / "memory-router" / "src")
)

from memory_router.contracts import Capabilities, Health, HealthStatus
from memory_router.registry import Registry


class FakeStoreSearchBackend:
    def capabilities(self) -> Capabilities:
        return Capabilities(
            name="store-search",
            verbs=frozenset({"store", "search"}),
            namespaces=("/global", "/projects/*"),
        )

    def health(self) -> Health:
        return Health(status=HealthStatus.OK)

    def store(self, req):
        raise NotImplementedError

    def search(self, req):
        raise NotImplementedError


class FakeSearchOnlyBackend:
    def capabilities(self) -> Capabilities:
        return Capabilities(
            name="search-only",
            verbs=frozenset({"search"}),
            namespaces=("/global", "/projects/*"),
        )

    def health(self) -> Health:
        return Health(status=HealthStatus.OK)

    def store(self, req):
        raise NotImplementedError

    def search(self, req):
        raise NotImplementedError


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.store_search = FakeStoreSearchBackend()
        self.search_only = FakeSearchOnlyBackend()
        self.registry = Registry(backends=[self.store_search, self.search_only])

    def test_search_dispatch_includes_all_capable_adapters(self):
        selected = self.registry.backends_for(verb="search", namespace="/global")
        self.assertEqual({self.store_search, self.search_only}, set(selected))

    def test_store_dispatch_excludes_search_only_adapter(self):
        selected = self.registry.backends_for(verb="store", namespace="/global")
        self.assertEqual([self.store_search], selected)

    def test_dispatch_respects_namespace_pattern(self):
        selected = self.registry.backends_for(
            verb="store", namespace="/agents/jarvis"
        )
        self.assertEqual([], selected)

    def test_dispatch_matches_project_wildcard_namespace(self):
        selected = self.registry.backends_for(
            verb="store", namespace="/projects/lector-ine"
        )
        self.assertEqual([self.store_search], selected)

    def test_empty_registry_returns_no_backends(self):
        empty = Registry(backends=[])
        self.assertEqual([], empty.backends_for(verb="search", namespace="/global"))


if __name__ == "__main__":
    unittest.main()
