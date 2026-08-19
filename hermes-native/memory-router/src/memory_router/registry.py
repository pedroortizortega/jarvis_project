import fnmatch
from importlib.metadata import entry_points

from .contracts import MemoryBackend

ENTRY_POINT_GROUP = "memory_router.backends"


def _namespace_matches(namespace: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(namespace, pattern) for pattern in patterns)


class Registry:
    """Loads backend adapters (Phase 1: only Engram) via the
    `memory_router.backends` entry-point group and selects, per request,
    only the adapters that declare the required capability.
    """

    def __init__(self, backends: list[MemoryBackend] | None = None):
        self._backends = (
            list(backends) if backends is not None else self._load_entry_points()
        )

    @staticmethod
    def _load_entry_points() -> list[MemoryBackend]:
        backends = []
        for entry_point in entry_points(group=ENTRY_POINT_GROUP):
            backend_class = entry_point.load()
            backends.append(backend_class())
        return backends

    def backends_for(self, *, verb: str, namespace: str) -> list[MemoryBackend]:
        selected = []
        for backend in self._backends:
            capabilities = backend.capabilities()
            if verb not in capabilities.verbs:
                continue
            if not _namespace_matches(namespace, capabilities.namespaces):
                continue
            selected.append(backend)
        return selected

    def all_backends(self) -> list[MemoryBackend]:
        return list(self._backends)
