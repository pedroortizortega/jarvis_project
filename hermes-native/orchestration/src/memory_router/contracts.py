from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable


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
