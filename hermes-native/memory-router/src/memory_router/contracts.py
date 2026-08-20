from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


class BackendUnavailableError(Exception):
    """Raised by an adapter when its backend cannot serve a request (e.g. the
    subprocess crashed). The dispatcher treats this as "degraded", never as
    a request failure."""

    def __init__(self, backend: str, reason: str):
        self.backend = backend
        self.reason = reason
        super().__init__(f"backend {backend!r} unavailable: {reason}")


class HealthStatus(Enum):
    OK = "ok"
    DEGRADED = "degraded"
    DOWN = "down"
@dataclass(frozen=True)
class Health:
    status: HealthStatus
    reason: str = ""


@dataclass(frozen=True)
class Capabilities:
    name: str
    verbs: frozenset[str]
    namespaces: tuple[str, ...]
    hierarchical_search: bool = True


@dataclass(frozen=True)
class StoreRequest:
    namespace: str
    role: str
    content: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class StoreResult:
    status: str  # "committed" | "pending"
    backend: str
    id: str


@dataclass(frozen=True)
class SearchRequest:
    namespace: str
    role: str
    query: str


@dataclass(frozen=True)
class SearchHit:
    namespace: str
    backend: str
    content: str
    score: float = 0.0


@dataclass(frozen=True)
class SearchResult:
    hits: tuple = ()
    unavailable: tuple = ()
@runtime_checkable
class MemoryBackend(Protocol):
    def capabilities(self) -> Capabilities: ...

    def health(self) -> Health: ...

    def store(self, req: StoreRequest) -> StoreResult: ...

    def search(self, req: SearchRequest) -> SearchResult: ...


@dataclass(frozen=True)
class ReflectRequest:
    namespace: str
    role: str
    query: str = ""


@dataclass(frozen=True)
class Conclusion:
    namespace: str
    backend: str
    content: str
    confidence: float = 0.0


@dataclass(frozen=True)
class ReflectResult:
    status: str  # "ready" | "pending" | "empty"
    backend: str
    conclusions: tuple = ()  # tuple[Conclusion]
    reason: str = ""


@runtime_checkable
class ReflectiveBackend(Protocol):
    """Separate, narrow contract for backends that support the `reflect`
    verb. Deliberately NOT part of `MemoryBackend` (see design.md
    "Architecture Decisions") — registry verb selection is the dispatch
    gate, so `MemoryBackend` conformance for Engram and Hindsight stays
    untouched and neither is required to implement `reflect()`."""

    def capabilities(self) -> Capabilities: ...

    def health(self) -> Health: ...

    def reflect(self, req: ReflectRequest) -> ReflectResult: ...


@runtime_checkable
class SearchOnlyBackend(Protocol):
    """Narrow contract for read-only backends that serve `search` and
    nothing else. Deliberately NOT `MemoryBackend` (which mandates
    `store()`), same precedent as `ReflectiveBackend` — registry verb
    selection is the dispatch gate, not Protocol conformance."""

    def capabilities(self) -> Capabilities: ...

    def health(self) -> Health: ...

    def search(self, req: SearchRequest) -> SearchResult: ...
