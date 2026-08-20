# Delta for Memory Backend Adapters

## ADDED Requirements

### Requirement: Cognee Adapter

The system MUST ship a `CogneeBackend` adapter that reaches Cognee over HTTP transport (not stdio-subprocess), config-driven auth via environment variables, mirroring the Honcho adapter's shape.

`CogneeBackend.capabilities().verbs` MUST equal exactly `frozenset({"reflect"})`. `CogneeBackend.capabilities().namespaces` MUST equal exactly `("/projects/*",)`. The adapter MUST NOT implement or declare `store` or `search`.

#### Scenario: Cognee adapter declares reflect-only verbs

- GIVEN `CogneeBackend().capabilities()` is inspected
- WHEN `verbs` is read
- THEN it equals `frozenset({"reflect"})` exactly, and `"store" not in verbs` and `"search" not in verbs` both hold

#### Scenario: Cognee adapter declares only /projects/*

- GIVEN `CogneeBackend().capabilities()` is inspected
- WHEN `namespaces` is read
- THEN it equals `("/projects/*",)` exactly, so `reflect` requests on `/user/master`, `/agents/*`, or `/global` select no Cognee backend

#### Scenario: Cognee adapter reaches Cognee over HTTP using GRAPH_COMPLETION

- GIVEN a `reflect` request is routed to the Cognee adapter for a project namespace
- WHEN the adapter performs the query
- THEN it communicates with Cognee via an injectable HTTP transport using the `GRAPH_COMPLETION` search type (not `CHUNKS`), using bearer-token or no-auth per environment configuration, and returns a `ReflectResult`

#### Scenario: Cognee transport failure raises BackendUnavailableError

- GIVEN the Cognee HTTP endpoint is unreachable, returns a non-success status, or returns an undecodable response
- WHEN the adapter attempts `reflect`
- THEN it raises `BackendUnavailableError("cognee", reason)` and does not raise any other exception type

#### Scenario: Honcho and Cognee coexist on disjoint namespaces

- GIVEN both `HonchoBackend` (`/user/master`) and `CogneeBackend` (`/projects/*`) are registered
- WHEN `Registry.backends_for(verb="reflect", namespace=ns)` is evaluated for any namespace `ns`
- THEN at most one of {Honcho adapter, Cognee adapter} is present in the returned list, because their declared namespace patterns do not overlap

### Requirement: Cognee Empty-Graph Handling Never Fabricates a Conclusion

When a `reflect` query against Cognee succeeds at the transport level but the underlying knowledge graph has no populated content relevant to the request, the adapter MUST return an explicit `ReflectResult` with `status == "empty"` and MUST NOT synthesize, guess, or otherwise fabricate a `Conclusion`. Since Cognee's `/recall` call is synchronous, the adapter MUST NOT report `"pending"` for this case; `"pending"` is reserved for an explicit async signal, which Cognee's synchronous call does not produce.

#### Scenario: Empty graph yields explicit empty status

- GIVEN a project's Cognee knowledge graph has no populated content
- WHEN the adapter performs `reflect` on that project's namespace
- THEN the call succeeds at the transport level and the adapter returns a `ReflectResult` with `status == "empty"` and no fabricated `Conclusion`

#### Scenario: Unscored confidence on a successful conclusion

- GIVEN Cognee's `GRAPH_COMPLETION` returns synthesized prose with no numeric confidence score
- WHEN the adapter builds the `Conclusion`
- THEN it sets `confidence=0.0` to represent "unscored", never inventing a non-zero score

### Requirement: Cognee Namespace-to-Dataset Mapping, Fail-Closed, One Dataset Per Project

`CogneeBackend` MUST map each `/projects/{name}` namespace to exactly one Cognee dataset dedicated to that project (one dataset per project), never a dataset shared across multiple projects. If a namespace cannot be resolved to a legal Cognee dataset/scope identifier (e.g. an illegal character set, or a nested namespace `/projects/{name}/{sub}` that cannot be unambiguously mapped to the parent project's scope), the adapter MUST fail closed and MUST NOT fall back to a shared or default dataset.

#### Scenario: Project namespace maps to its own dataset

- GIVEN a `reflect` request targets `/projects/alpha`
- WHEN the adapter resolves the Cognee dataset
- THEN it resolves to a dataset scoped exclusively to `alpha`, distinct from any other project's dataset

#### Scenario: No cross-project leakage

- GIVEN two distinct projects `alpha` and `beta` each have populated Cognee datasets
- WHEN the adapter performs `reflect` on `/projects/alpha`
- THEN the returned `Conclusion` is derived only from `alpha`'s dataset, never `beta`'s

#### Scenario: Unresolvable namespace fails closed

- GIVEN a namespace cannot be mapped to a legal Cognee dataset/scope identifier
- WHEN the adapter attempts to resolve it
- THEN the adapter fails closed (raises rather than falling back to a shared or default dataset) and no request reaches Cognee with an ambiguous scope
