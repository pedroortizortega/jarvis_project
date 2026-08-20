# Memory Backend Adapters Specification

## Purpose

Define the backend adapter contract and Phase 1's single Engram adapter, including degraded-backend behavior.

## Requirements

### Requirement: Adapter Contract

Each backend adapter MUST declare its capabilities (e.g. supports store, supports search, supports health-check) and MUST implement a store interface, a search interface, and a health interface. The router MUST select backends per request only from adapters that declare the required capability.

#### Scenario: Router selects only capable adapters

- GIVEN a search request targets a namespace backed by two adapters, one of which does not declare search capability
- WHEN the router dispatches the search
- THEN only the adapter declaring search capability is queried

### Requirement: Multi-Adapter Backend Support

The system MUST support more than one concurrently registered backend adapter through the existing `memory_router.backends` entry-point group. Phase 1's "exactly one adapter" constraint is superseded: the Engram adapter (spec 011) remains the reference implementation of the `MemoryBackend` Protocol, and a second adapter — Hindsight — MUST be registrable through the same entry-point group with zero changes to `registry.py`, `contracts.py`, `app.py`, `permissions.py`, `namespaces.py`, `identity.py`, or `journal.py`.

Each registered adapter MUST remain fully default-constructible (zero required constructor arguments; configuration sourced from environment), matching the instantiation pattern in `Registry._load_entry_points` (`entry_point.load()()`).

#### Scenario: Engram adapter handles store and search

- GIVEN a request is routed to the Engram adapter
- WHEN the adapter performs store or search
- THEN it communicates with Engram via its existing supported access path and returns results/status through the adapter contract

#### Scenario: Two adapters register with no router-core changes

- GIVEN both the Engram adapter and the Hindsight adapter are installed and declared under the `memory_router.backends` entry-point group
- WHEN `Registry()` is constructed with no explicit `backends` argument
- THEN `Registry.all_backends()` returns both adapter instances, and no file under router core (`app.py`, `registry.py`, `contracts.py`, `permissions.py`, `namespaces.py`, `identity.py`, `journal.py`) was modified to enable this

#### Scenario: Registered adapter is default-constructible

- GIVEN an adapter class is registered under the `memory_router.backends` entry-point group
- WHEN the registry loads it via `entry_point.load()()` (zero arguments)
- THEN construction succeeds without requiring any explicit constructor argument, with all configuration resolved from environment variables at construction time

### Requirement: Degraded Backend — Store Queues Instead of Dropping

When the target backend for a `store` request is unavailable, the router MUST queue/buffer the write and respond with an explicit "pending" status. The router MUST NOT respond with a committed-success status, MUST NOT respond with a generic failure status, and MUST NOT drop the write.

#### Scenario: Store queued when backend is down

- GIVEN the Engram backend is unavailable
- WHEN a client issues `POST /memory/store` with a validly declared, permitted namespace
- THEN the router queues the write and responds with an explicit "pending" status, not `200` committed and not a `5xx` failure

### Requirement: Degraded Backend — Search Returns Partial Results

When one or more backends required for a `search` are unavailable, the router MUST return available results from healthy backends plus an explicit per-backend "unavailable" marker. The router MUST NOT fail the entire search solely because one backend is down.

#### Scenario: Partial search results with unavailable marker

- GIVEN a search spans a namespace whose backend is unavailable while another in-scope namespace's backend is healthy
- WHEN the router processes the search
- THEN it returns results from the healthy backend along with an explicit marker identifying the unavailable backend, and the request does not fail outright

### Requirement: Hindsight Adapter

The system MUST ship a `HindsightBackend` adapter implementing the `MemoryBackend` Protocol (`capabilities()`, `health()`, `store()`, `search()`) that reaches Hindsight over HTTP transport, not the stdio-subprocess transport used by the Engram adapter.

#### Scenario: Hindsight adapter handles store and search over HTTP

- GIVEN a request is routed to the Hindsight adapter
- WHEN the adapter performs store or search
- THEN it communicates with Hindsight via an HTTP client (not a subprocess) and returns results/status through the adapter contract

### Requirement: Hindsight Declared Verbs Exclude Reflect

`HindsightBackend.capabilities().verbs` MUST equal exactly `{"store", "search"}`. The adapter MUST NOT declare `"reflect"` as a supported verb.

#### Scenario: Reflect is absent from declared verbs

- GIVEN `HindsightBackend().capabilities()` is inspected
- WHEN `verbs` is read
- THEN it equals `frozenset({"store", "search"})` and `"reflect" not in verbs` holds

### Requirement: Hindsight Namespace-to-Bank Mapping Without Cross-Backend Overlap

`HindsightBackend` MUST map each Memory Router namespace to a Hindsight memory bank (`bank_id`), analogous to the Engram adapter's `ns:` topic-key prefix. The namespace patterns declared in `capabilities().namespaces` MUST NOT overlap the namespace patterns declared by the Engram adapter's `capabilities().namespaces`, so that the router's `Registry.backends_for()` selection never returns both adapters for the same namespace on the same verb, avoiding double-write on `store` and duplicate/fan-out results on `search`.

#### Scenario: Namespace maps to a Hindsight bank

- GIVEN a `store` or `search` request targets a namespace matched by `HindsightBackend.capabilities().namespaces`
- WHEN the adapter builds the outbound Hindsight request
- THEN the namespace is mapped to a corresponding `bank_id` and included in the request sent to Hindsight

#### Scenario: No dual dispatch between Engram and Hindsight

- GIVEN both adapters are registered
- WHEN `Registry.backends_for(verb="store", namespace=ns)` or `Registry.backends_for(verb="search", namespace=ns)` is evaluated for any namespace `ns`
- THEN at most one of {Engram adapter, Hindsight adapter} is present in the returned list, because their declared namespace patterns do not overlap

### Requirement: Hindsight Config-Driven Auth

`HindsightBackend` MUST support both local no-auth access and Hindsight Cloud bearer-token access, selected by configuration (environment variables) at construction time, with no hardcoded auth mode in code.

#### Scenario: Local no-auth mode

- GIVEN the adapter's environment configuration does not set a Hindsight auth token
- WHEN the adapter issues an HTTP request to a local/no-auth Hindsight instance
- THEN no `Authorization` header is sent and the request proceeds

#### Scenario: Bearer-token mode

- GIVEN the adapter's environment configuration sets a Hindsight Cloud auth token
- WHEN the adapter issues an HTTP request
- THEN the request includes an `Authorization: Bearer <token>` header sourced from that configuration

### Requirement: Hindsight Transport Failure Integrates With Degraded-Backend Handling

When the Hindsight adapter's HTTP transport fails (connection error, non-success status, or response decode failure), the adapter MUST raise `BackendUnavailableError("hindsight", ...)`. This MUST integrate, unchanged, with the existing dispatcher degraded-backend behavior: a `store` request queues as "pending" and a `search` request returns partial results with an explicit "unavailable" marker for the Hindsight backend.

#### Scenario: Hindsight HTTP failure raises BackendUnavailableError

- GIVEN the Hindsight HTTP endpoint is unreachable, returns a non-success status, or returns an undecodable response
- WHEN the adapter attempts `store` or `search`
- THEN it raises `BackendUnavailableError("hindsight", reason)` and does not raise any other exception type

#### Scenario: Store queued when Hindsight is down

- GIVEN the Hindsight backend is unavailable
- WHEN a client issues `POST /memory/store` with a validly declared, permitted namespace routed to the Hindsight adapter
- THEN the router queues the write and responds with an explicit "pending" status, not `200` committed and not a `5xx` failure

#### Scenario: Partial search results when Hindsight is down

- GIVEN a search spans a namespace whose backend is the unavailable Hindsight adapter, while another in-scope namespace's backend is healthy
- WHEN the router processes the search
- THEN it returns results from the healthy backend along with an explicit marker identifying Hindsight as unavailable, and the request does not fail outright

### Requirement: Honcho Adapter

The system MUST ship a `HonchoBackend` adapter that reaches Honcho's Dialectic API over HTTP transport (not stdio-subprocess), config-driven auth via environment variables, mirroring the Hindsight adapter's shape.

`HonchoBackend.capabilities().verbs` MUST equal exactly `frozenset({"reflect"})`. `HonchoBackend.capabilities().namespaces` MUST equal exactly `("/user/master",)`. The adapter MUST NOT implement or declare `store` or `search`.

#### Scenario: Honcho adapter declares reflect-only verbs

- GIVEN `HonchoBackend().capabilities()` is inspected
- WHEN `verbs` is read
- THEN it equals `frozenset({"reflect"})` exactly, and `"store" not in verbs` and `"search" not in verbs` both hold

#### Scenario: Honcho adapter declares only /user/master

- GIVEN `HonchoBackend().capabilities()` is inspected
- WHEN `namespaces` is read
- THEN it equals `("/user/master",)` exactly, so `reflect` requests on `/projects/*` or `/agents/*` select no Honcho backend

#### Scenario: Honcho adapter reaches Honcho over HTTP

- GIVEN a `reflect` request is routed to the Honcho adapter
- WHEN the adapter performs the Dialectic query
- THEN it communicates with Honcho via an injectable HTTP transport, using bearer-token or no-auth per environment configuration, and returns a `ReflectResult`

#### Scenario: Honcho transport failure raises BackendUnavailableError

- GIVEN the Honcho HTTP endpoint is unreachable, returns a non-success status, or returns an undecodable response
- WHEN the adapter attempts `reflect`
- THEN it raises `BackendUnavailableError("honcho", reason)` and does not raise any other exception type

### Requirement: Reflect-Capable Backend Contract Is Separate From MemoryBackend

The system MUST expose a narrow, capability-gated contract (a distinct Protocol) for backends that support `reflect`, separate from the existing `MemoryBackend` Protocol (`capabilities()`, `health()`, `store()`, `search()`).

The `MemoryBackend` Protocol MUST NOT gain a default or no-op `reflect()` method. The dispatcher MUST reach a backend's `reflect()` only through registry selection gated on `capabilities().verbs` containing `"reflect"`, never by structurally assuming every `MemoryBackend` implements `reflect()`.

Existing adapter conformance MUST remain unmodified and passing: `isinstance(EngramBackend(), MemoryBackend)` and `isinstance(HindsightBackend(), MemoryBackend)` MUST still hold true, and neither adapter is required to implement `reflect()`.

#### Scenario: Reflect-capable Protocol is distinct from MemoryBackend

- GIVEN the reflect-capable contract and `MemoryBackend` are inspected
- WHEN their method sets are compared
- THEN `MemoryBackend` has no `reflect()` method, and the reflect-capable contract is a separate Protocol that `HonchoBackend` satisfies

#### Scenario: Engram and Hindsight conformance is unaffected

- GIVEN `EngramBackend` and `HindsightBackend` as they exist before this change
- WHEN `isinstance(EngramBackend(), MemoryBackend)` and `isinstance(HindsightBackend(), MemoryBackend)` are evaluated after this change
- THEN both still return `True`, with their existing conformance tests unmodified

#### Scenario: Dispatcher gates reflect dispatch on declared capability

- GIVEN a registered backend does not declare `"reflect"` in `capabilities().verbs`
- WHEN `Registry.backends_for(verb="reflect", namespace=...)` is evaluated
- THEN that backend is never returned and the dispatcher never calls `reflect()` on it
