# Delta for Memory Router Interfaces

## ADDED Requirements

### Requirement: Search-Only Backend Contract Is Separate From MemoryBackend

The system MUST expose a narrow, capability-gated `SearchOnlyBackend` Protocol (`capabilities()`, `health()`, `search()`) for backends that support `search` but not `store`, mirroring the existing precedent set by `ReflectiveBackend` for `reflect`-only backends. `SearchOnlyBackend` MUST NOT require or declare a `store()` method.

The `MemoryBackend` Protocol MUST NOT gain a default or no-op behavior to accommodate search-only adapters. The dispatcher MUST reach a backend's `search()` only through registry selection gated on `capabilities().verbs` containing `"search"`, never by structurally assuming every registered backend implements `store()`.

Existing adapter conformance MUST remain unmodified and passing: `isinstance(EngramBackend(), MemoryBackend)` and `isinstance(HindsightBackend(), MemoryBackend)` MUST still hold true.

#### Scenario: SearchOnlyBackend is distinct from MemoryBackend

- GIVEN `SearchOnlyBackend` and `MemoryBackend` are inspected
- WHEN their method sets are compared
- THEN `SearchOnlyBackend` has no `store()` method, and it is a separate Protocol that a search-only adapter (e.g. `KnowledgeVaultBackend`) satisfies

#### Scenario: A search-only adapter is not a MemoryBackend

- GIVEN an adapter implements only `capabilities()`, `health()`, and `search()`
- WHEN `isinstance(adapter, SearchOnlyBackend)` and `isinstance(adapter, MemoryBackend)` are evaluated
- THEN the first is `True` and the second is `False`, because `MemoryBackend` requires `store()`

#### Scenario: Existing MemoryBackend conformance is unaffected

- GIVEN `EngramBackend` and `HindsightBackend` as they exist before this change
- WHEN `isinstance(EngramBackend(), MemoryBackend)` and `isinstance(HindsightBackend(), MemoryBackend)` are evaluated after this change
- THEN both still return `True`, with their existing conformance tests unmodified

#### Scenario: Dispatcher gates search dispatch on declared capability

- GIVEN a registered backend does not declare `"search"` in `capabilities().verbs`
- WHEN `Registry.backends_for(verb="search", namespace=...)` is evaluated
- THEN that backend is never returned and the dispatcher never calls `search()` on it
