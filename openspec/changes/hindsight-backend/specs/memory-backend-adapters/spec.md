# Memory Backend Adapters — Delta Spec (hindsight-backend)

Delta against `openspec/specs/memory-backend-adapters/spec.md`.

## MODIFIED Requirements

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

## ADDED Requirements

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
