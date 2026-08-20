# Delta for Memory Backend Adapters

## ADDED Requirements

### Requirement: Graphiti Adapter

The system MUST ship a `GraphitiBackend` adapter that reaches Graphiti over HTTP transport (not stdio-subprocess), config-driven auth via environment variables, mirroring the Cognee adapter's shape (injectable `transport(method, url, headers, body)` seam).

`GraphitiBackend.capabilities().verbs` MUST equal exactly `frozenset({"reflect"})`. `GraphitiBackend.capabilities().namespaces` MUST equal exactly `("/global", "/agents/*")`. The adapter MUST NOT implement or declare `store` or `search`.

#### Scenario: Graphiti adapter declares reflect-only verbs

- GIVEN `GraphitiBackend().capabilities()` is inspected
- WHEN `verbs` is read
- THEN it equals `frozenset({"reflect"})` exactly, and `"store" not in verbs` and `"search" not in verbs` both hold

#### Scenario: Graphiti adapter declares only /global and /agents/*

- GIVEN `GraphitiBackend().capabilities()` is inspected
- WHEN `namespaces` is read
- THEN it equals `("/global", "/agents/*")` exactly, so `reflect` requests on `/user/master` or `/projects/*` select no Graphiti backend

#### Scenario: Graphiti adapter reaches Graphiti over HTTP using search_facts

- GIVEN a `reflect` request is routed to the Graphiti adapter for `/global` or an `/agents/{name}` namespace
- WHEN the adapter performs the query
- THEN it communicates with Graphiti via an injectable HTTP transport calling `search_facts` (not `search_nodes`), using bearer-token or no-auth per environment configuration, and returns a `ReflectResult`

#### Scenario: Graphiti transport failure raises BackendUnavailableError

- GIVEN the Graphiti HTTP endpoint is unreachable, returns a non-success status, or returns an undecodable response
- WHEN the adapter attempts `reflect`
- THEN it raises `BackendUnavailableError("graphiti", reason)` and does not raise any other exception type

#### Scenario: Graphiti, Honcho, and Cognee coexist on disjoint namespaces

- GIVEN `HonchoBackend` (`/user/master`), `CogneeBackend` (`/projects/*`), and `GraphitiBackend` (`/global`, `/agents/*`) are all registered
- WHEN `Registry.backends_for(verb="reflect", namespace=ns)` is evaluated for any namespace `ns`
- THEN at most one of {Honcho adapter, Cognee adapter, Graphiti adapter} is present in the returned list, because their declared namespace patterns do not overlap

### Requirement: Graphiti Empty-Graph Handling Never Fabricates a Conclusion

When a `reflect` query against Graphiti succeeds at the transport level but `search_facts` returns no facts relevant to the request (e.g. an unpopulated graph, since no ingestion path exists in this change), the adapter MUST return an explicit `ReflectResult` with `status == "empty"` and MUST NOT synthesize, guess, or otherwise fabricate a `Conclusion`.

#### Scenario: Empty or unpopulated graph yields explicit empty status

- GIVEN a `group_id`'s Graphiti graph has no ingested episodes or no facts relevant to the query
- WHEN the adapter performs `reflect` on that namespace
- THEN the call succeeds at the transport level and the adapter returns a `ReflectResult` with `status == "empty"` and no fabricated `Conclusion`

#### Scenario: Unscored confidence on a successful conclusion

- GIVEN Graphiti's `search_facts` returns relevance-ranked facts with no calibrated confidence score
- WHEN the adapter builds the `Conclusion`
- THEN it sets `confidence=0.0` to represent "unscored", matching the Cognee adapter's convention, never inventing a non-zero score

### Requirement: Graphiti Namespace-to-Group Mapping, Fail-Closed, One Group Per Agent

`GraphitiBackend` MUST map `/global` to one fixed shared `group_id` and each `/agents/{name}` namespace to its own dedicated `group_id` (one group per agent, never a group shared across multiple agents). If a namespace cannot be resolved to a legal `group_id` (e.g. an illegal character set, or a nested namespace `/agents/{name}/{sub}` that cannot be unambiguously mapped to the parent agent's scope), the adapter MUST fail closed and MUST NOT fall back to a shared or default group.

#### Scenario: /global maps to the fixed shared group

- GIVEN a `reflect` request targets `/global`
- WHEN the adapter resolves the Graphiti `group_id`
- THEN it resolves to the one fixed shared group reserved for `/global`

#### Scenario: Agent namespace maps to its own group

- GIVEN a `reflect` request targets `/agents/alpha`
- WHEN the adapter resolves the Graphiti `group_id`
- THEN it resolves to a group scoped exclusively to `alpha`, distinct from any other agent's group

#### Scenario: No cross-agent leakage

- GIVEN two distinct agents `alpha` and `beta` each have populated Graphiti groups
- WHEN the adapter performs `reflect` on `/agents/alpha`
- THEN the returned `Conclusion` is derived only from `alpha`'s group, never `beta`'s

#### Scenario: Unresolvable namespace fails closed

- GIVEN a namespace cannot be mapped to a legal Graphiti `group_id` (illegal characters, or an unmappable nested `/agents/{name}/{sub}` path)
- WHEN the adapter attempts to resolve it
- THEN the adapter fails closed (raises rather than falling back to a shared or default group) and no request reaches Graphiti with an ambiguous scope

### Requirement: Graphiti Only Currently-Valid Facts Are Returned

Graphiti facts carry `valid_at`/`invalid_at` temporal-validity bounds. The adapter MUST filter to only currently-valid facts (facts whose validity interval includes the query time) when building a `Conclusion`. Expired facts MUST NOT be included, and the discarded temporal interval MUST NOT be silently reinterpreted as part of `Conclusion.content` beyond the fact's verbatim text.

#### Scenario: Expired fact is excluded from the conclusion

- GIVEN `search_facts` returns both a currently-valid fact and a fact whose `invalid_at` has passed
- WHEN the adapter builds the `Conclusion`
- THEN only the currently-valid fact's text is included, and the expired fact is excluded entirely
